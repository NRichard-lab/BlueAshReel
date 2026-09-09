"""Narrow encrypted RPC to local services. Never proxy arbitrary HTTP or filesystem paths."""

from __future__ import annotations

import asyncio
import hashlib
import os
import secrets
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse
from sqlalchemy import delete, select, tuple_, update

from app.api import catalog, playback
from app.api import router as administration
from app.config import AppConfig
from app.dependencies import Principal
from app.models import (
    Library,
    PlaybackSession,
    PortalGrant,
    RemoteObject,
    Role,
    ScanLock,
    User,
    UserLibrary,
    UserSession,
    utcnow,
)
from app.remote.protocol import encode
from app.remote.storage import read_json, write_json
from app.schemas import LibraryCreate, LibraryPathCreate, LibraryUpdate, ScanRequest
from app.services.catalog import safe_text
from app.services.compatibility import PlaybackChoice
from app.services.media_roots import _ensure_not_protected
from app.services.paths import assert_no_link_components, native_directory_guard, validate_windows_path_text
from app.services.playback import load_playback
from app.services.transcoding_policy import TranscodingPolicy, read_policy

MAX_CHUNK = 131072
MAX_MANIFEST = 2 * 1048576
MAX_MANIFEST_SNAPSHOTS = 8
MAX_MANIFEST_CACHE = 8 * 1048576
MANIFEST_SNAPSHOT_TTL = 45


@dataclass(frozen=True)
class ManifestSnapshot:
    # Portal authentication sessions survive renewal of the encrypted relay.
    binding: tuple[str, str, str, str]
    expires_at: float
    body: bytes


def identifier(value: Any) -> str:
    if not isinstance(value, str) or str(uuid.UUID(value)) != value:
        raise ValueError("Invalid identifier")
    return value


def integer(value: Any, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise ValueError("Invalid bounded integer")
    return value


class RemoteMedia:
    def __init__(self, config: AppConfig, factory: Any, manager: Any) -> None:
        self.config, self.factory, self.manager = config, factory, manager
        self.agent_id = ""
        self.owner_id = ""
        # Segment aliases and short-lived manifest snapshots stay in memory;
        # source objects use persistent random mappings in the Agent database.
        self.segments: dict[str, tuple[str, str]] = {}
        self.manifest_snapshots: dict[str, ManifestSnapshot] = {}
        self._manifest_lock = threading.Lock()
        self.folder_selections: dict[str, str] = {}

    def bind(self, agent_id: str, owner_id: str) -> None:
        self.agent_id, self.owner_id = identifier(agent_id), identifier(owner_id)

    def alias(self, db: Any, kind: str, local_id: str) -> str:
        row = db.scalar(
            select(RemoteObject).where(
                RemoteObject.agent_id == self.agent_id, RemoteObject.kind == kind, RemoteObject.local_id == local_id
            )
        )
        if row is None:
            row = RemoteObject(agent_id=self.agent_id, kind=kind, local_id=local_id)
            db.add(row)
            db.flush()
        if row.revoked:
            raise HTTPException(404, "Unavailable")
        return identifier(row.id)

    def resolve(self, db: Any, kind: str, opaque: Any) -> str:
        row = db.get(RemoteObject, identifier(opaque))
        if row is None or row.agent_id != self.agent_id or row.kind != kind or row.revoked:
            raise HTTPException(404, "Unavailable")
        return identifier(row.local_id)

    def external(self, db: Any, value: Any, kind: str = "media",
                 aliases: dict[tuple[str, str], str] | None = None) -> Any:
        if isinstance(value, list):
            return [self.external(db, item, kind, aliases) for item in value]
        if not isinstance(value, dict):
            return value
        result = {}
        ids = {
            "id": kind,
            "library_id": "library",
            "file_id": "file",
            "show_id": "media",
            "job_id": "job",
            "active_job_id": "job",
            "media_id": "media",
            "session_id": "playback",
        }
        for key, item in value.items():
            if key in {"poster_url", "background_url"}:
                result["artwork_id" if key == "poster_url" else "background_id"] = (
                    (aliases[("artwork", item.rsplit("/", 1)[-1])] if aliases is not None
                     else self.alias(db, "artwork", item.rsplit("/", 1)[-1])) if item else None
                )
            elif key == "error_summary":
                result[key] = "The local scan encountered an error. Review the Agent logs." if item else None
            elif key in {"url", "analysis_error", "fingerprint"}:
                # Raw errors may contain local paths. Only stable error codes
                # cross this boundary, encrypted or otherwise.
                continue
            elif key in ids and item is not None:
                result[key] = aliases[(ids[key], item)] if aliases is not None else self.alias(db, ids[key], item)
            else:
                child_kind = "file" if key == "files" else "path" if key == "paths" else (
                    "library" if key == "libraries" else kind
                )
                result[key] = self.external(db, item, child_kind, aliases)
        return result

    def external_home(self, db: Any, result: dict[str, Any]) -> dict[str, Any]:
        # Home can repeat a title in several rails. Resolve its opaque IDs in
        # batches rather than querying once for every field on every card.
        references = {("library", item["id"]) for item in result["libraries"]}
        for key, rail in result.items():
            if key == "libraries":
                continue
            for item in rail:
                for field, kind in (("id", "media"), ("library_id", "library"),
                                    ("file_id", "file"), ("show_id", "media")):
                    if item.get(field):
                        references.add((kind, item[field]))
                if item.get("poster_url"):
                    references.add(("artwork", item["poster_url"].rsplit("/", 1)[-1]))
        aliases = {}
        ordered = sorted(references)
        for offset in range(0, len(ordered), 400):
            for row in db.scalars(select(RemoteObject).where(
                RemoteObject.agent_id == self.agent_id,
                tuple_(RemoteObject.kind, RemoteObject.local_id).in_(ordered[offset:offset + 400]),
            )):
                if row.revoked:
                    raise HTTPException(404, "Unavailable")
                aliases[(row.kind, row.local_id)] = row.id
        missing = []
        for kind, local_id in sorted(references - aliases.keys()):
            row = RemoteObject(id=str(uuid.uuid4()), agent_id=self.agent_id, kind=kind, local_id=local_id)
            missing.append(row)
            aliases[(kind, local_id)] = row.id
        if missing:
            db.add_all(missing)
            db.flush()
        return self.external(db, result, aliases=aliases)

    def principal(self, db: Any, authorization: dict[str, Any]) -> Principal:
        if (
            authorization.get("authorized") is not True
            or authorization.get("agent_id") != self.agent_id
            or authorization.get("expires_at", 0) <= time.time()
        ):
            raise HTTPException(403, "Access denied")
        user_id = identifier(authorization["user_id"])
        role = authorization.get("role")
        grant = db.get(PortalGrant, user_id)
        if user_id == self.owner_id and role == "owner":
            if grant is None:
                # Preserve the original Owner's local watch history on migration.
                owners = list(db.scalars(select(User).join(User.roles).where(Role.name == "Owner").limit(2)))
                if len(owners) > 1:
                    raise HTTPException(409, "Local Owner migration requires an unambiguous account")
                user = owners[0] if owners else None
                if user is None:
                    user = self.new_user(db, user_id)
                grant = PortalGrant(
                    portal_user_id=user_id,
                    agent_id=self.agent_id,
                    local_user_id=user.id,
                    role="owner",
                    enabled=True,
                    access_version=1,
                )
                db.add(grant)
                db.flush()
            elif grant.agent_id != self.agent_id and grant.enabled and grant.role == "owner":
                # A locally confirmed re-pair can register this installation
                # under a new Agent ID for the same Portal Owner. Only the
                # current paired Owner's verified grant may follow that change;
                # retain its local account and watch history, never member grants.
                grant.agent_id = self.agent_id
            current = set(db.scalars(select(UserLibrary.library_id).where(UserLibrary.user_id == grant.local_user_id)))
            for library_id in db.scalars(select(Library.id)):
                if library_id not in current:
                    db.add(UserLibrary(user_id=grant.local_user_id, library_id=library_id))
        if (
            grant is None
            or grant.agent_id != self.agent_id
            or not grant.enabled
            or grant.role != role
            or (role != "owner" and grant.access_version != authorization.get("access_version"))
        ):
            raise HTTPException(403, "Local library permission required")
        user = db.get(User, grant.local_user_id)
        if user is None or not user.is_active:
            raise HTTPException(403, "Access denied")
        portal_session = identifier(authorization["session_id"])
        session = db.get(UserSession, portal_session)
        if session is None:
            session = UserSession(
                id=portal_session,
                user_id=user.id,
                token_hash=hashlib.sha256(secrets.token_bytes(32)).hexdigest(),
                csrf_hash=hashlib.sha256(secrets.token_bytes(32)).hexdigest(),
                expires_at=datetime.fromtimestamp(authorization["expires_at"], UTC),
            )
            db.add(session)
        elif session.user_id != user.id or session.revoked_at is not None:
            raise HTTPException(403, "Access denied")
        else:
            session.expires_at = datetime.fromtimestamp(authorization["expires_at"], UTC)
        db.commit()
        return Principal(user=user, session=session)

    @staticmethod
    def new_user(db: Any, portal_user_id: str) -> User:
        user = User(
            username="Portal member",
            normalized_username="portal-" + portal_user_id,
            password_hash="!portal-only",  # noqa: S106 - deliberately invalid; no local password credential exists
            is_active=True,
        )
        db.add(user)
        db.flush()
        return user

    @staticmethod
    def owner(authorization: dict[str, Any]) -> None:
        if authorization.get("role") != "owner":
            raise HTTPException(403, "Owner access required")

    async def dispatch(self, request: dict[str, Any], authorization: dict[str, Any]) -> dict[str, Any]:
        request_id = request.get("id")
        try:
            identifier(request_id)
            if set(request) - {"id", "op", "payload"} or not isinstance(request.get("payload", {}), dict):
                raise ValueError("Invalid request")
            if request.get("op") == "folders.select":
                self.owner(authorization)
                with self.factory() as db:
                    self.principal(db, authorization)
                from app.native_consent import request_folder

                supplied = request.get("payload", {})
                if set(supplied) - {"manual_path"}:
                    raise ValueError("Invalid folder request")
                manual = supplied.get("manual_path")
                if manual is not None and (not isinstance(manual, str) or len(manual) > 2048):
                    raise ValueError("Invalid path")
                selected = await request_folder(self.config, manual_path=manual)
                if selected is None:
                    raise HTTPException(409, "Local confirmation required")
                with self.factory() as db:
                    self.principal(db, authorization)
                result = self.approve_folder(Path(selected))
            else:
                result = await asyncio.to_thread(self.execute, request, authorization)
            return {"id": request_id, "ok": True, "result": result}
        except HTTPException as error:
            code = {
                401: "access_denied",
                403: "access_denied",
                404: "not_found",
                409: "conflict",
                410: "session_expired",
                422: "invalid_request",
                429: "rate_limited",
            }.get(error.status_code, "operation_failed")
        except (ValueError, TypeError, KeyError):
            code = "invalid_request"
        except (TimeoutError, ImportError):
            code = "local_confirmation_required"
        except Exception:
            code = "operation_failed"
        return {"id": request_id, "ok": False, "error": code}

    def approve_folder(self, path: Path) -> dict[str, Any]:
        if self.config.deployment_mode != "native_windows":
            raise HTTPException(409, "Native local confirmation required")
        if os.name == "nt":
            validate_windows_path_text(str(path))
        if not path.is_absolute() or path == Path(path.anchor):
            raise ValueError("Invalid source root")
        with native_directory_guard(path):
            assert_no_link_components(path)
            path = path.resolve(strict=True)
            _ensure_not_protected(path, self.config)
            with os.scandir(path) as entries:
                next(entries, None)
        location = self.config.app_data_dir / "remote-roots.json"
        roots = read_json(location).get("roots", [])
        for existing in self.config.approved_media_roots:
            if path == existing.path.resolve(strict=False) or path.is_relative_to(existing.path):
                if len(self.folder_selections) >= 100:
                    raise ValueError("Folder selection limit")
                token = str(uuid.uuid4())
                self.folder_selections[token] = str(path)
                return {"selection_id": token, "path": str(path)}
            if existing.path.is_relative_to(path):
                raise ValueError("Approved roots cannot overlap")
        if len(roots) >= 100:
            raise ValueError("Approved root limit exceeded")
        root = {"id": str(uuid.uuid4()), "display_name": "Approved media", "path": str(path)}
        roots.append(root)
        import json

        if len(json.dumps({"roots": roots}).encode()) > 30000:
            raise ValueError("Approved root storage limit")
        write_json(location, {"roots": roots})
        return {"selection_id": root["id"], "path": str(path)}

    def selected_paths(self, selected: Any) -> list[str]:
        if not isinstance(selected, list) or not 1 <= len(selected) <= 16:
            raise ValueError("Select approved folders")
        roots = {root.id: root.path for root in self.config.approved_media_roots}
        roots.update({key: Path(value) for key, value in self.folder_selections.items()})
        return [str(roots[identifier(value)]) for value in selected]

    def execute(self, request: dict[str, Any], authorization: dict[str, Any]) -> Any:
        try:
            return self._execute(request, authorization)
        finally:
            # A conversion admitted while the relay delivered revocation must
            # also be stopped after its bounded startup has returned.
            if authorization.get("revoked"):
                self.release(authorization)

    def release(self, authorization: dict[str, Any]) -> None:
        with self.factory() as db:
            rows = list(
                db.scalars(
                    select(PlaybackSession).where(
                        PlaybackSession.auth_session_id == authorization["session_id"],
                        PlaybackSession.state == "active",
                    )
                )
            )
            for row in rows:
                row.state, row.ended_at, row.was_playing = "stopped", utcnow(), False
                self.forget_playback(row.id)
                self.manager.stop(row.id)
            db.commit()
        with self._manifest_lock:
            self.manifest_snapshots = {
                key: value
                for key, value in self.manifest_snapshots.items()
                if value.binding[2] != authorization["session_id"]
            }

    def _execute(self, request: dict[str, Any], authorization: dict[str, Any]) -> Any:
        op, args = request.get("op"), request.get("payload", {})
        with self.factory() as db:
            principal = self.principal(db, authorization)
            page = integer(args.get("page", 1), 1, 1000000)
            size = integer(args.get("page_size", 24), 1, 24)
            kind = "media"
            if op == "status":
                result = {"health": "ok", "mode": "relay", "remote_media_available": True}
            elif op in {"transcoding.get", "transcoding.update", "transcoding.test"}:
                self.owner(authorization)
                policy = read_policy(db, self.config)
                if op == "transcoding.update":
                    if set(args) - {"mode", "preferred_hardware"} or args.get("mode") not in {
                        "automatic", "hardware_preferred", "software_only"
                    }:
                        raise ValueError("Invalid transcoding settings")
                    policy = TranscodingPolicy.model_validate({**policy.model_dump(), **args})
                    playback.update_transcoding_policy(policy, principal, db, self.config, self.manager)
                elif op == "transcoding.test":
                    if args or policy.mode == "software_only":
                        raise ValueError("Hardware tests are disabled in Software Only")
                    self.manager.detect_hardware(policy)
                health = self.manager.health()
                return {
                    "mode": policy.mode, "preferred_hardware": policy.preferred_hardware,
                    "selected_encoder": health["selected_encoder"], "fallback": health["software_fallback"],
                    "fallback_reason": health["failure"],
                    "hardware_tests": [{k: value[k] for k in ("encoder", "test_status", "last_test_at", "failure")}
                                       for value in health["hardware_tests"]],
                }
            elif op == "streams.list":
                self.owner(authorization)
                result = playback.streams(principal, db, self.manager)
                result.pop("health", None)
                kind = "playback"
            elif op == "streams.stop":
                self.owner(authorization)
                session_id = self.resolve(db, "playback", args["session_id"])
                playback.owner_stop(session_id, principal, db, self.manager)
                self.forget_playback(session_id)
                return {"stopped": True}
            elif op == "catalog.home":
                if args:
                    raise ValueError("Home does not accept catalog or filesystem selectors")
                result = self.external_home(db, catalog.home(principal, db))
                db.commit()
                return result
            elif op == "catalog.list":
                media_kind, history, query = args.get("kind"), args.get("history"), args.get("q", "")
                if (
                    media_kind not in {None, "movie", "series", "episode", "other"}
                    or history not in {None, "continue", "recent"}
                    or not isinstance(query, str)
                    or len(query) > 200
                ):
                    raise ValueError("Invalid view")
                result = catalog.catalog(
                    page,
                    size,
                    media_kind,
                    query,
                    self.resolve(db, "library", args["library_id"]) if args.get("library_id") else None,
                    "title",
                    None,
                    None,
                    None,
                    history,
                    principal,
                    db,
                )
            elif op == "catalog.detail":
                result = catalog.detail(self.resolve(db, "media", args["media_id"]), page, principal, db)
            elif op == "catalog.seasons":
                result = catalog.seasons(self.resolve(db, "media", args["media_id"]), page, size, principal, db)
                kind = "season"
            elif op == "catalog.episodes":
                result = catalog.episodes(self.resolve(db, "season", args["season_id"]), page, size, principal, db)
            elif op == "catalog.next":
                result = catalog.next_episode(self.resolve(db, "media", args["media_id"]), principal, db)
            elif op == "artwork.bytes":
                resource = catalog.artwork(self.resolve(db, "artwork", args["artwork_id"]), principal, db, self.config)
                return self.chunk(Path(resource.path), args, resource.media_type)
            elif op in {"playback.start", "playback.decision"}:
                if args.get("delivery", "auto") != "auto":
                    self.owner(authorization)
                choice_args = {key: value for key, value in args.items() if key not in {"page", "page_size"}}
                choice_args["file_id"] = self.resolve(db, "file", args["file_id"])
                if args.get("recovery_from"):
                    choice_args["recovery_from"] = self.resolve(db, "playback", args["recovery_from"])
                choice = PlaybackChoice.model_validate(choice_args)
                if op == "playback.decision":
                    result = jsonable_encoder(playback.decision(choice, principal, db, self.config))
                else:
                    result = jsonable_encoder(
                        playback.create_playback(choice, principal, db, self.config, self.manager)
                    )
                    result["resource"] = "file" if result["decision"]["method"] == "direct" else "manifest"
                kind = "playback"
            elif op in {
                "playback.bytes",
                "playback.progress",
                "playback.stop",
                "playback.state",
                "playback.manifest.release",
            }:
                session_id = self.resolve(db, "playback", args["session_id"])
                if op == "playback.bytes":
                    return self.playback_bytes(db, principal, session_id, args)
                if op == "playback.manifest.release":
                    if set(args) != {"session_id", "snapshot_id"}:
                        raise ValueError("Invalid manifest release")
                    load_playback(db, principal, session_id, self.config)
                    return self.release_manifest(principal, session_id, identifier(args["snapshot_id"]))
                if op == "playback.progress":
                    progress_args = {key: value for key, value in args.items() if key != "session_id"}
                    result = playback.progress(
                        session_id, playback.ProgressInput.model_validate(progress_args), principal, db, self.config
                    )
                elif op == "playback.stop":
                    playback.stop(session_id, principal, db, self.manager)
                    self.forget_playback(session_id)
                    result = {"stopped": True}
                else:
                    result = playback.playback_state(session_id, principal, db, self.config)
                kind = "playback"
            elif isinstance(op, str) and op.startswith("libraries."):
                self.owner(authorization)
                result, kind = self.libraries(db, principal, op, args, page, size)
            elif op == "grants.set":
                self.owner(authorization)
                result = self.grant(db, args)
            elif op == "grants.get":
                self.owner(authorization)
                result = self.get_grant(db, args)
            elif op == "grants.list":
                self.owner(authorization)
                result = {
                    "items": [
                        {
                            "user_id": grant.portal_user_id,
                            "role": grant.role,
                            "enabled": grant.enabled,
                            "access_version": grant.access_version,
                            "library_ids": [
                                self.alias(db, "library", item)
                                for item in db.scalars(
                                    select(UserLibrary.library_id).where(UserLibrary.user_id == grant.local_user_id)
                                )
                            ],
                        }
                        for grant in db.scalars(
                            select(PortalGrant)
                            .where(PortalGrant.agent_id == self.agent_id)
                            .offset((page - 1) * size)
                            .limit(size)
                        )
                    ]
                }
                db.commit()
                return result
            else:
                raise ValueError("Unsupported operation")
            result = self.external(db, jsonable_encoder(result), kind)
            db.commit()
            return result

    def libraries(self, db: Any, principal: Principal, op: str, args: Any, page: int, size: int) -> tuple[Any, str]:
        if op == "libraries.list":
            return administration.list_libraries(page, size, principal, db, self.config), "library"
        if op == "libraries.create":
            payload = LibraryCreate(
                name=args["name"], library_type=args["library_type"], paths=self.selected_paths(args["selection_ids"])
            )
            return administration.create_library(payload, principal, db, self.config), "library"
        library_id = self.resolve(db, "library", args["library_id"])
        if op == "libraries.errors":
            from sqlalchemy import func

            from app.models import MediaFile, MediaItem

            query = (
                select(MediaFile)
                .join(MediaItem)
                .where(MediaItem.library_id == library_id, MediaFile.analysis_error.is_not(None))
            )
            total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
            rows = db.scalars(query.order_by(MediaFile.id).offset((page - 1) * size).limit(size))
            return {
                "items": [
                    {
                        "file_id": row.id,
                        "media_id": row.media_item_id,
                        "message": safe_text(row.analysis_error) or "Local file analysis failed. Check Agent logs.",
                    }
                    for row in rows
                ],
                "total": total,
                "page": page,
                "page_size": size,
            }, "file"
        if op == "libraries.update":
            administration.update_library(
                library_id,
                LibraryUpdate.model_validate({key: args[key] for key in ("name", "enabled") if key in args}),
                principal,
                db,
                self.config,
            )
            if args.get("add_selection_ids"):
                for path in self.selected_paths(args["add_selection_ids"]):
                    administration.add_library_path(
                        library_id, LibraryPathCreate(path=path), principal, db, self.config
                    )
            removed = args.get("remove_path_ids", [])
            if not isinstance(removed, list) or len(removed) > 16:
                raise ValueError("Invalid paths")
            for path_id in removed:
                administration.remove_library_path(library_id, self.resolve(db, "path", path_id), principal, db)
            return administration.get_library(library_id, principal, db, self.config), "library"
        if op == "libraries.delete":
            if db.get(ScanLock, library_id):
                raise HTTPException(409, "Stop the active scan first")
            db.execute(delete(Library).where(Library.id == library_id))
            db.execute(
                update(RemoteObject)
                .where(
                    RemoteObject.agent_id == self.agent_id,
                    RemoteObject.kind == "library",
                    RemoteObject.local_id == library_id,
                )
                .values(revoked=True)
            )
            db.commit()
            return {"removed": True}, "library"
        if op == "libraries.scan":
            return administration.start_scan(
                library_id, ScanRequest(mode=args.get("mode", "changed")), principal, db
            ), "job"
        lock = db.get(ScanLock, library_id)
        if op == "libraries.stop":
            return (
                {"status": "idle"} if lock is None else administration.cancel_job(lock.job_id, principal, db)
            ), "job"
        if op == "libraries.status":
            from app.models import BackgroundJob, ScanJob

            job = db.scalar(
                select(BackgroundJob)
                .join(ScanJob)
                .where(ScanJob.library_id == library_id)
                .order_by(BackgroundJob.created_at.desc())
                .limit(1)
            )
            return administration._job_public(job) if job else {"status": "idle"}, "job"
        raise ValueError("Unsupported operation")

    def get_grant(self, db: Any, args: dict[str, Any]) -> dict[str, Any]:
        if set(args) != {"user_id"}:
            raise ValueError("Invalid permission request")
        user_id = identifier(args["user_id"])
        if user_id == self.owner_id:
            raise HTTPException(403, "Owner access is not a member grant")
        grant = db.get(PortalGrant, user_id)
        if grant is None:
            return {"user_id": user_id, "library_ids": []}
        if grant.agent_id != self.agent_id:
            raise HTTPException(404, "Grant unavailable")
        if grant.role == "owner":
            raise HTTPException(403, "Owner access is not a member grant")
        library_ids = list(
            db.scalars(
                select(UserLibrary.library_id)
                .where(UserLibrary.user_id == grant.local_user_id)
                .order_by(UserLibrary.library_id)
                .limit(101)
            )
        )
        if len(library_ids) > 100:
            # Never make unreturned permissions appear unchecked and allow an
            # apparently harmless save to silently drop existing access.
            raise HTTPException(409, "This local grant exceeds the library selection limit")
        return {
            "user_id": user_id,
            "role": grant.role,
            "enabled": grant.enabled,
            "access_version": grant.access_version,
            "library_ids": [self.alias(db, "library", library_id) for library_id in library_ids],
        }

    def grant(self, db: Any, args: dict[str, Any]) -> dict[str, Any]:
        user_id = identifier(args["user_id"])
        role = args.get("role", "viewer")
        enabled = args.get("enabled", True)
        version = integer(args.get("access_version", 1), 1, 2147483647)
        if user_id == self.owner_id or role not in {"viewer", "manager"} or type(enabled) is not bool:
            raise ValueError("Invalid permission")
        selected = args.get("library_ids", [])
        if not isinstance(selected, list) or len(selected) > 100:
            raise ValueError("Invalid library selection")
        libraries = {self.resolve(db, "library", value) for value in selected}
        row = db.get(PortalGrant, user_id)
        if row is None:
            user = self.new_user(db, user_id)
            row = PortalGrant(
                portal_user_id=user_id, agent_id=self.agent_id, local_user_id=user.id, role=role, access_version=version
            )
            db.add(row)
        elif row.agent_id != self.agent_id or version < row.access_version:
            raise HTTPException(409, "Stale permission")
        row.role, row.enabled, row.access_version = role, enabled, version
        db.execute(delete(UserLibrary).where(UserLibrary.user_id == row.local_user_id))
        for library_id in libraries if enabled else []:
            db.add(UserLibrary(user_id=row.local_user_id, library_id=library_id))
        # Every update invalidates active playback immediately; detailed grants
        # are checked again on every encrypted request and byte range.
        active = list(
            db.scalars(
                select(PlaybackSession).where(
                    PlaybackSession.user_id == row.local_user_id, PlaybackSession.state == "active"
                )
            )
        )
        for playback_row in active:
            playback_row.state, playback_row.ended_at = "stopped", utcnow()
            self.forget_playback(playback_row.id)
            self.manager.stop(playback_row.id)
        db.commit()
        return {"user_id": user_id, "enabled": enabled, "role": role, "access_version": version}

    @staticmethod
    def chunk(source: Path | bytes, args: Any, mime: str | None) -> dict[str, Any]:
        offset = integer(args.get("offset", 0), 0, 2**53 - 1)
        length = integer(args.get("length", MAX_CHUNK), 1, MAX_CHUNK)
        total = len(source) if isinstance(source, bytes) else source.stat().st_size
        if offset > total:
            raise HTTPException(422, "Invalid byte range")
        if isinstance(source, bytes):
            body = source[offset : offset + length]
        else:
            with source.open("rb") as stream:
                stream.seek(offset)
                body = stream.read(length)
        return {
            "data": encode(body),
            "total": total,
            "offset": offset,
            "mime": mime or "application/octet-stream",
            "eof": offset + len(body) >= total,
        }

    def forget_playback(self, session_id: str) -> None:
        with self._manifest_lock:
            self.segments = {key: value for key, value in self.segments.items() if value[0] != session_id}
            self.manifest_snapshots = {
                key: value for key, value in self.manifest_snapshots.items() if value.binding[3] != session_id
            }

    def manifest_binding(self, principal: Principal, session_id: str) -> tuple[str, str, str, str]:
        return self.agent_id, principal.user.id, principal.session.id, session_id

    def prune_manifests(self) -> None:
        # Callers hold the lock; expiry is fixed rather than extended by reads.
        now = time.monotonic()
        self.manifest_snapshots = {
            key: value for key, value in self.manifest_snapshots.items() if value.expires_at > now
        }

    def prune_playback_resources(self, db: Any) -> None:
        # The playback watchdog also expires abandoned sessions without an RPC
        # stop. Reclaim their aliases before applying the shared resource cap;
        # active sessions must survive ordinary encrypted transport renewal.
        active = set(db.scalars(select(PlaybackSession.id).where(PlaybackSession.state == "active")))
        self.segments = {key: value for key, value in self.segments.items() if value[0] in active}
        self.manifest_snapshots = {
            key: value for key, value in self.manifest_snapshots.items() if value.binding[3] in active
        }

    def release_manifest(self, principal: Principal, session_id: str, snapshot_id: str) -> dict[str, bool]:
        with self._manifest_lock:
            self.prune_manifests()
            snapshot = self.manifest_snapshots.get(snapshot_id)
            if snapshot is not None and snapshot.binding != self.manifest_binding(principal, session_id):
                raise HTTPException(404, "Manifest snapshot unavailable")
            self.manifest_snapshots.pop(snapshot_id, None)
        return {"released": True}

    def render_manifest(self, db: Any, principal: Principal, session_id: str) -> bytes:
        response = playback.hls_file(session_id, "index.m3u8", principal, db, self.config, self.manager)
        raw = bytes(response.body)
        if len(raw) > MAX_MANIFEST:
            raise HTTPException(422, "Manifest exceeds safety limit")
        reverse = {name: token for token, (sid, name) in self.segments.items() if sid == session_id}
        output = []
        for line in raw.decode("utf-8").splitlines():
            if line and not line.startswith("#"):
                token = reverse.get(line)
                if token is None:
                    if len(self.segments) >= 16384:
                        raise HTTPException(429, "Segment limit")
                    token = str(uuid.uuid4())
                    self.segments[token] = (session_id, line)
                    reverse[line] = token
                output.append(token)
            elif "URI=" in line:
                raise HTTPException(422, "Unsupported manifest reference")
            else:
                output.append(line)
        body = ("\n".join(output) + "\n").encode()
        if len(body) > MAX_MANIFEST:
            raise HTTPException(422, "Manifest exceeds safety limit")
        return body

    def manifest_chunk(self, db: Any, principal: Principal, session_id: str, args: Any) -> dict[str, Any]:
        snapshot_id = identifier(args["snapshot_id"]) if "snapshot_id" in args else None
        offset = integer(args.get("offset", 0), 0, 2**53 - 1)
        integer(args.get("length", MAX_CHUNK), 1, MAX_CHUNK)
        with self._manifest_lock:
            self.prune_manifests()
            self.prune_playback_resources(db)
            snapshot = self.manifest_snapshots.get(snapshot_id) if snapshot_id else None
            binding = self.manifest_binding(principal, session_id)
            if snapshot is not None:
                if snapshot.binding != binding:
                    raise HTTPException(404, "Manifest snapshot unavailable")
                body = snapshot.body
            else:
                if snapshot_id and offset != 0:
                    # Never splice a newly grown manifest into a partial reply.
                    raise HTTPException(410, "Manifest snapshot expired; retry from the beginning")
                if snapshot_id and len(self.manifest_snapshots) >= MAX_MANIFEST_SNAPSHOTS:
                    raise HTTPException(429, "Manifest snapshot limit")
                body = self.render_manifest(db, principal, session_id)
                if snapshot_id:
                    cached_size = sum(len(item.body) for item in self.manifest_snapshots.values())
                    if cached_size + len(body) > MAX_MANIFEST_CACHE:
                        raise HTTPException(429, "Manifest snapshot capacity")
                    self.manifest_snapshots[snapshot_id] = ManifestSnapshot(
                        binding, time.monotonic() + MANIFEST_SNAPSHOT_TTL, body
                    )
            return self.chunk(body, args, "application/vnd.apple.mpegurl")

    def playback_bytes(self, db: Any, principal: Principal, session_id: str, args: Any) -> dict[str, Any]:
        row, file, source = load_playback(db, principal, session_id, self.config)
        resource = args.get("resource")
        if "snapshot_id" in args and resource != "manifest":
            raise ValueError("Only manifests support snapshots")
        # Watch credit is measured since the previous progress checkpoint.
        # Byte fetches must not reset that clock: prefetching is not watching.
        if resource == "file" and row.method == "direct":
            # Revalidate authorized_file and source fingerprint for every range.
            return self.chunk(source, args, str(row.decision.get("mime", "video/mp4")))
        if resource == "subtitles" and row.subtitle_index is not None:
            response = playback.subtitle(session_id, row.subtitle_index, principal, db, self.config, self.manager)
            return self.chunk(bytes(response.body), args, "text/vtt")
        if resource == "manifest":
            return self.manifest_chunk(db, principal, session_id, args)
        if resource == "segment":
            with self._manifest_lock:
                segment = self.segments.get(identifier(args.get("segment")))
            if segment is None or segment[0] != session_id:
                raise HTTPException(404, "Unavailable")
            response = playback.hls_file(session_id, segment[1], principal, db, self.config, self.manager)
            if not isinstance(response, FileResponse):
                raise HTTPException(404, "Unavailable")
            return self.chunk(Path(response.path), args, "video/mp2t")
        raise HTTPException(404, "Unavailable")
