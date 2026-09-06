from __future__ import annotations

import asyncio
import json
import os
import subprocess
import time
import uuid
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import HTTPException
from test_playback import CAPS, playable
from test_remote_access import browser_session, encrypted_request

from app.main import app
from app.models import (
    BackgroundJob,
    Library,
    LocalArtwork,
    MediaFile,
    PlaybackSession,
    PortalGrant,
    SubtitleStream,
    UserLibrary,
    WatchProgress,
)
from app.remote.connector import Connector
from app.remote.media import (
    MANIFEST_SNAPSHOT_TTL,
    MAX_CHUNK,
    MAX_MANIFEST,
    MAX_MANIFEST_CACHE,
    MAX_MANIFEST_SNAPSHOTS,
    RemoteMedia,
)
from app.remote.protocol import decode


def setup_remote(owner_context):
    context, csrf = owner_context
    local_library, local_item, local_file, source = playable(context, csrf)
    media = RemoteMedia(context.config, context.session_factory, app.state.playback_manager)
    agent_id, owner_id, session_id = (str(uuid.uuid4()) for _ in range(3))
    media.bind(agent_id, owner_id)
    authorization = {
        "authorized": True,
        "agent_id": agent_id,
        "user_id": owner_id,
        "session_id": session_id,
        "role": "owner",
        "access_version": 1,
        "expires_at": time.time() + 800,
    }
    return media, authorization, (local_library, local_item, local_file, source)


def call(media, authorization, op, **payload):
    return media.execute({"id": str(uuid.uuid4()), "op": op, "payload": payload}, authorization)


@pytest.fixture
def growing_manifest(owner_context, tmp_path, monkeypatch):
    media, auth, _ = setup_remote(owner_context)
    card = call(media, auth, "catalog.list")["items"][0]
    directory = tmp_path / "synthetic-hls"
    directory.mkdir()
    manifest = directory / "index.m3u8"
    manifest.write_text("#EXTM3U\n#EXTINF:4.000000,\nsegment-000000.ts\n", encoding="utf-8")
    # Use the real authorized HLS file reader with a disposable growing file;
    # these boundary tests do not require a long-running FFmpeg process.
    monkeypatch.setattr(media.manager, "for_session", lambda _sid: SimpleNamespace(directory=directory))

    def start(authorization=auth):
        result = call(media, authorization, "playback.start", file_id=card["file_id"], capabilities=CAPS)
        with media.factory() as db:
            internal = media.resolve(db, "playback", result["id"])
            db.get(PlaybackSession, internal).method = "remux"
            db.commit()
        return result["id"]

    return media, auth, card, manifest, start


def test_large_manifest_snapshot_stays_stable_during_growth_and_reauthorization(growing_manifest):
    media, auth, _card, manifest, start = growing_manifest
    session_id, snapshot_id = start(), str(uuid.uuid4())
    manifest.write_text(
        "#EXTM3U\n" + "".join(f"#EXTINF:4.000000,\nsegment-{index:06d}.ts\n" for index in range(4000)),
        encoding="utf-8",
    )
    args = {"session_id": session_id, "resource": "manifest", "snapshot_id": snapshot_id}
    first = call(media, auth, "playback.bytes", **args)
    expected = media.manifest_snapshots[snapshot_id].body
    assert len(expected) > MAX_CHUNK and first["total"] == len(expected) and not first["eof"]
    with manifest.open("a", encoding="utf-8") as stream:
        stream.write("#EXTINF:4.000000,\nsegment-004000.ts\n#EXT-X-ENDLIST\n")
    fresh = {**auth, "expires_at": time.time() + 899, "sid": str(uuid.uuid4())}
    received = decode(first["data"])
    while len(received) < first["total"]:
        part = call(media, fresh, "playback.bytes", **args, offset=len(received), length=32000)
        assert part["total"] == first["total"] and part["offset"] == len(received)
        received += decode(part["data"])
    assert part["eof"] and received == expected
    assert b"segment-" not in received and b"#EXT-X-ENDLIST" not in received
    updated = call(media, fresh, "playback.bytes", **{**args, "snapshot_id": str(uuid.uuid4())})
    assert updated["total"] > first["total"]


@pytest.mark.parametrize("other", ["playback", "portal_session", "user", "agent"])
def test_manifest_snapshot_rejects_other_bindings(growing_manifest, other):
    media, auth, card, _manifest, start = growing_manifest
    original, snapshot_id = start(), str(uuid.uuid4())
    call(media, auth, "playback.bytes", session_id=original, resource="manifest", snapshot_id=snapshot_id)
    target, authorization = original, auth
    if other == "playback":
        target = start()
    elif other == "portal_session":
        authorization = {**auth, "session_id": str(uuid.uuid4())}
        target = start(authorization)
    elif other == "user":
        authorization = {**auth, "user_id": str(uuid.uuid4()), "session_id": str(uuid.uuid4()), "role": "viewer"}
        call(media, auth, "grants.set", user_id=authorization["user_id"], library_ids=[card["library_id"]])
        target = start(authorization)
    else:
        authorization = {**auth, "agent_id": str(uuid.uuid4())}
    for op, extra in (("playback.bytes", {"resource": "manifest"}), ("playback.manifest.release", {})):
        with pytest.raises(HTTPException) as denied:
            call(media, authorization, op, session_id=target, snapshot_id=snapshot_id, **extra)
        assert denied.value.status_code in {403, 404}
    assert snapshot_id in media.manifest_snapshots


def test_manifest_snapshots_expire_without_splicing_new_contents(growing_manifest, monkeypatch):
    media, auth, _card, manifest, start = growing_manifest
    clock = [100.0]
    monkeypatch.setattr("app.remote.media.time.monotonic", lambda: clock[0])
    args = {"session_id": start(), "resource": "manifest", "snapshot_id": str(uuid.uuid4())}
    initial = call(media, auth, "playback.bytes", **args, length=8)
    clock[0] += MANIFEST_SNAPSHOT_TTL - 1
    call(media, auth, "playback.bytes", **args, offset=8, length=1)
    clock[0] += 1
    with pytest.raises(HTTPException) as expired:
        call(media, auth, "playback.bytes", **args, offset=9)
    assert expired.value.status_code == 410 and not media.manifest_snapshots
    with manifest.open("a", encoding="utf-8") as stream:
        stream.write("#EXT-X-ENDLIST\n")
    assert call(media, auth, "playback.bytes", **args)["total"] > initial["total"]


@pytest.mark.parametrize("limit", ["count", "aggregate", "individual"])
def test_manifest_snapshot_storage_bounds(growing_manifest, limit):
    media, auth, _card, manifest, start = growing_manifest
    args = {"session_id": start(), "resource": "manifest"}
    if limit != "count":
        # The extra segment makes the opaque representation exceed 2 MiB even
        # when the original local manifest is just below its own size limit.
        if limit == "individual":
            raw = b"#EXTM3U\n#" + b"x" * (MAX_MANIFEST - 29) + b"\nsegment-000000.ts\n"
            assert len(raw) <= MAX_MANIFEST
        else:
            raw = b"#EXTM3U\n#" + b"x" * (MAX_MANIFEST - 10) + b"\n"
            assert len(raw) == MAX_MANIFEST
        manifest.write_bytes(raw)
    admitted = MAX_MANIFEST_SNAPSHOTS if limit == "count" else MAX_MANIFEST_CACHE // MAX_MANIFEST
    if limit != "individual":
        for _ in range(admitted):
            call(media, auth, "playback.bytes", **args, snapshot_id=str(uuid.uuid4()))
    with pytest.raises(HTTPException) as rejected:
        call(media, auth, "playback.bytes", **args, snapshot_id=str(uuid.uuid4()))
    assert rejected.value.status_code == (422 if limit == "individual" else 429)
    assert len(media.manifest_snapshots) <= MAX_MANIFEST_SNAPSHOTS
    assert sum(len(snapshot.body) for snapshot in media.manifest_snapshots.values()) <= MAX_MANIFEST_CACHE


def test_manifest_release_is_idempotent_and_reuses_capacity(growing_manifest):
    media, auth, _card, _manifest, start = growing_manifest
    session_id = start()
    for _ in range(MAX_MANIFEST_SNAPSHOTS * 2):
        snapshot_id = str(uuid.uuid4())
        call(media, auth, "playback.bytes", session_id=session_id, resource="manifest", snapshot_id=snapshot_id)
        for _attempt in range(2):
            assert call(media, auth, "playback.manifest.release", session_id=session_id, snapshot_id=snapshot_id) == {
                "released": True
            }
        assert not media.manifest_snapshots


@pytest.mark.parametrize("cleanup", ["stop", "revocation", "grant"])
def test_manifest_cleanup_and_authorization_on_cached_chunks(growing_manifest, cleanup):
    media, auth, card, _manifest, start = growing_manifest
    authorization = auth
    if cleanup == "grant":
        authorization = {**auth, "user_id": str(uuid.uuid4()), "session_id": str(uuid.uuid4()), "role": "viewer"}
        call(media, auth, "grants.set", user_id=authorization["user_id"], library_ids=[card["library_id"]])
    args = {"session_id": start(authorization), "resource": "manifest", "snapshot_id": str(uuid.uuid4())}
    call(media, authorization, "playback.bytes", **args, length=8)
    with pytest.raises(HTTPException) as expired:
        call(media, {**authorization, "expires_at": 0}, "playback.bytes", **args, offset=8)
    assert expired.value.status_code == 403
    if cleanup == "stop":
        call(media, authorization, "playback.stop", session_id=args["session_id"])
    elif cleanup == "revocation":
        media.release(authorization)
    else:
        call(media, auth, "grants.set", user_id=authorization["user_id"], enabled=False, access_version=2)
    assert not media.manifest_snapshots and not media.segments
    with pytest.raises(HTTPException):
        call(media, authorization, "playback.bytes", **args, offset=8)


def test_manifest_snapshot_input_bounds_and_legacy_compatibility(growing_manifest):
    media, auth, _card, _manifest, start = growing_manifest
    args = {"session_id": start(), "resource": "manifest"}
    for extra in ({"snapshot_id": "../unsafe"}, {"snapshot_id": None}, {"offset": -1}, {"length": MAX_CHUNK + 1}):
        with pytest.raises(ValueError):
            call(media, auth, "playback.bytes", **args, **extra)
    with pytest.raises(HTTPException) as missing:
        call(media, auth, "playback.bytes", **args, snapshot_id=str(uuid.uuid4()), offset=1)
    assert missing.value.status_code == 410 and not media.manifest_snapshots
    # Callers without snapshot support keep the original byte-reply contract.
    legacy = call(media, auth, "playback.bytes", **args)
    assert decode(legacy["data"]).startswith(b"#EXTM3U") and legacy["eof"]
    assert not media.manifest_snapshots


@pytest.mark.parametrize("abandoned_state", ["expired", "deleted"])
def test_abandoned_playback_aliases_are_reclaimed_before_capacity_check(growing_manifest, abandoned_state):
    media, auth, _card, manifest, start = growing_manifest
    abandoned, old_snapshot = start(), str(uuid.uuid4())
    manifest.write_text(
        "#EXTM3U\n" + "".join(f"#EXTINF:4.000000,\nsegment-{index:06d}.ts\n" for index in range(16383)),
        encoding="utf-8",
    )
    call(media, auth, "playback.bytes", session_id=abandoned, resource="manifest", snapshot_id=old_snapshot)
    manifest.write_text("#EXTM3U\n#EXTINF:4.000000,\nsegment-000000.ts\n", encoding="utf-8")
    active, kept_snapshot = start(), str(uuid.uuid4())
    kept = call(media, auth, "playback.bytes", session_id=active, resource="manifest", snapshot_id=kept_snapshot)
    assert len(media.segments) == 16384
    with media.factory() as db:
        row = db.get(PlaybackSession, media.resolve(db, "playback", abandoned))
        # Simulate watchdog expiry or later history retention: neither invokes
        # RemoteMedia's explicit stop/release handler when a browser vanishes.
        if abandoned_state == "deleted":
            db.delete(row)
        else:
            row.state = abandoned_state
        db.commit()
    newest = start()
    call(media, auth, "playback.bytes", session_id=newest, resource="manifest", snapshot_id=str(uuid.uuid4()))
    assert len(media.segments) == 2 and old_snapshot not in media.manifest_snapshots
    fresh = {**auth, "expires_at": time.time() + 899, "sid": str(uuid.uuid4())}
    renewed = call(media, fresh, "playback.bytes", session_id=active, resource="manifest", snapshot_id=kept_snapshot)
    assert renewed == kept


def test_remote_catalog_aliases_and_bounded_range_playback(owner_context):
    media, auth, (local_library, local_item, local_file, source) = setup_remote(owner_context)
    result = call(media, auth, "catalog.list")
    assert len(result["items"]) == 1
    card = result["items"][0]
    assert card["id"] != local_item and card["file_id"] != local_file
    assert card["library_id"] != local_library["id"]
    assert uuid.UUID(card["id"]).version == 4
    detail = call(media, auth, "catalog.detail", media_id=card["id"])
    assert detail["files"][0]["id"] == card["file_id"]
    assert str(source) not in json.dumps(detail)
    start = call(media, auth, "playback.start", file_id=card["file_id"], capabilities=CAPS)
    assert start["resource"] == "file" and "url" not in start
    chunk = call(media, auth, "playback.bytes", session_id=start["id"], resource="file", offset=256, length=256)
    assert decode(chunk["data"]) == bytes(range(256))
    assert chunk["total"] == source.stat().st_size and chunk["offset"] == 256
    assert not chunk["eof"]
    final = call(
        media,
        auth,
        "playback.bytes",
        session_id=start["id"],
        resource="file",
        offset=source.stat().st_size - 7,
        length=MAX_CHUNK,
    )
    assert len(decode(final["data"])) == 7 and final["eof"]
    call(
        media,
        auth,
        "playback.progress",
        session_id=start["id"],
        position_seconds=10,
        playing=True,
        reason="seek",
        sequence=1,
    )
    call(media, auth, "playback.stop", session_id=start["id"])
    with pytest.raises(HTTPException):
        call(media, auth, "playback.bytes", session_id=start["id"], resource="file")


def test_enumeration_cross_agent_and_local_grants(owner_context):
    media, auth, (_, local_item, _, _) = setup_remote(owner_context)
    card = call(media, auth, "catalog.list")["items"][0]
    for guessed in (local_item, str(uuid.uuid4()), "1", "../../media"):
        with pytest.raises((HTTPException, ValueError)):
            call(media, auth, "catalog.detail", media_id=guessed)
    other = {**auth, "user_id": str(uuid.uuid4()), "session_id": str(uuid.uuid4()), "role": "viewer"}
    with pytest.raises(HTTPException, match="Local library"):
        call(media, other, "catalog.list")
    call(media, auth, "grants.set", user_id=other["user_id"], role="viewer", library_ids=[], access_version=1)
    assert call(media, other, "catalog.list")["items"] == []
    call(
        media,
        auth,
        "grants.set",
        user_id=other["user_id"],
        role="viewer",
        library_ids=[card["library_id"]],
        access_version=1,
    )
    assert call(media, other, "catalog.list")["items"][0]["id"] == card["id"]
    with pytest.raises(HTTPException):
        call(media, other, "libraries.list")
    with pytest.raises(HTTPException):
        call(media, {**other, "access_version": 2}, "catalog.list")
    media.bind(str(uuid.uuid4()), auth["user_id"])
    with media.factory() as db, pytest.raises(HTTPException):
        media.resolve(db, "media", card["id"])


def test_revoked_local_access_stops_playback_before_next_bytes(owner_context):
    media, auth, _ = setup_remote(owner_context)
    card = call(media, auth, "catalog.list")["items"][0]
    viewer = {**auth, "user_id": str(uuid.uuid4()), "session_id": str(uuid.uuid4()), "role": "viewer"}
    call(
        media,
        auth,
        "grants.set",
        user_id=viewer["user_id"],
        role="viewer",
        library_ids=[card["library_id"]],
        access_version=1,
    )
    start = call(media, viewer, "playback.start", file_id=card["file_id"], capabilities=CAPS)
    call(
        media,
        auth,
        "grants.set",
        user_id=viewer["user_id"],
        role="viewer",
        library_ids=[],
        enabled=False,
        access_version=2,
    )
    with pytest.raises(HTTPException):
        call(media, viewer, "playback.bytes", session_id=start["id"], resource="file")


@pytest.mark.parametrize("invalid", [{"page_size": 25}, {"page_size": True}, {"q": "x" * 201}, {"kind": "path"}])
def test_remote_view_bounds(owner_context, invalid):
    media, auth, _ = setup_remote(owner_context)
    with pytest.raises(ValueError):
        call(media, auth, "catalog.list", **invalid)


def test_encrypted_dispatch_hides_media_and_sanitizes_errors(owner_context):
    media, auth, _ = setup_remote(owner_context)
    session, incoming, _, _, _, _ = browser_session()
    request = {"id": str(uuid.uuid4()), "op": "catalog.list", "payload": {"q": "Local"}}
    frame = encrypted_request(session, incoming, request)
    opened = session.open(frame)
    result = asyncio.run(media.dispatch(opened, auth))
    assert result["ok"] is True and result["result"]["items"][0]["title"] == "Local"
    sealed = session.seal(result)
    assert "Local" not in json.dumps(sealed)
    malformed = asyncio.run(
        media.dispatch({"id": str(uuid.uuid4()), "op": "read_file", "payload": {"path": "C:/private-secret"}}, auth)
    )
    assert malformed["error"] == "invalid_request"
    assert "private-secret" not in json.dumps(malformed)


def test_chunk_limits_and_source_changes(owner_context):
    media, auth, (_, _, _, source) = setup_remote(owner_context)
    card = call(media, auth, "catalog.list")["items"][0]
    start = call(media, auth, "playback.start", file_id=card["file_id"], capabilities=CAPS)
    for offset, length in ((-1, 1), (0, MAX_CHUNK + 1), (True, 1), (0, 0)):
        with pytest.raises(ValueError):
            call(media, auth, "playback.bytes", session_id=start["id"], resource="file", offset=offset, length=length)
    source.write_bytes(b"changed")
    with pytest.raises(HTTPException):
        call(media, auth, "playback.bytes", session_id=start["id"], resource="file")


def test_local_status_pkce_cookie_agent_binding_and_callback_replay(owner_context):
    context, _ = owner_context
    media, auth, _ = setup_remote(owner_context)
    connector = Connector(context.config.app_data_dir / "control", context.config.app_data_dir / "identity", "0.1.0")
    connector.prepare_identity("Synthetic")
    connector.credentials.update(agent_id=auth["agent_id"], user_id=auth["user_id"])
    connector.media = media
    app.state.portal_connector = connector
    app.state.portal_pending = {}
    app.state.portal_status_sessions = {}
    base = "http://127.0.0.1:18080"
    response = context.client.get(base + "/portal/start", follow_redirects=False)
    assert response.status_code == 303
    query = {key: values[0] for key, values in parse_qs(urlsplit(response.headers["location"]).fragment).items()}
    assert query["agent_id"] == auth["agent_id"] and len(query["code_challenge"]) == 43
    assert "verifier" not in response.headers["location"]
    payload = {"state": query["state"], "nonce": query["nonce"], "code": "synthetic-code-with-entropy"}
    headers = {"Origin": base}
    assert (
        context.client.post(
            base + "/portal/callback", json=payload, headers={"Origin": "https://untrusted.invalid"}
        ).status_code
        == 403
    )
    result = {
        **auth,
        "authorization_id": str(uuid.uuid4()),
        "purpose": "status",
        "state": query["state"],
        "nonce": query["nonce"],
        "callback": query["callback"],
        "code_challenge": query["code_challenge"],
    }
    with patch("app.remote.local_auth.post_control", return_value=result):
        response = context.client.post(base + "/portal/callback", json=payload, headers=headers)
        assert response.status_code == 200, response.text
        assert context.client.post(base + "/portal/callback", json=payload, headers=headers).status_code == 403
    with patch("app.remote.local_auth.post_control", side_effect=[{"nonce": "fresh-nonce"}, result]):
        assert context.client.get(base + "/portal/status").status_code == 200
    with patch("app.remote.local_auth.post_control", side_effect=[{"nonce": "fresh-nonce"}, {"authorized": False}]):
        assert context.client.get(base + "/portal/status", follow_redirects=False).status_code == 303
    assert context.client.get("http://evil.invalid/portal/start", follow_redirects=False).status_code == 403


def test_native_local_password_apis_are_redirected_to_portal(owner_context):
    context, _ = owner_context
    context.config.deployment_mode = "native_windows"
    for route in ("/", "/api/v1/auth/login", "/api/v1/setup/owner", "/api/v1/browse/media"):
        response = context.client.get(route, follow_redirects=False)
        assert response.status_code == 303 and response.headers["location"] == "/portal/start"


@pytest.fixture
def real_ffmpeg():
    candidate = os.getenv("TEST_FFMPEG_PATH")
    if not candidate:
        pytest.skip("Set TEST_FFMPEG_PATH to exercise real synthetic media")
    return candidate


@pytest.mark.parametrize(
    ("video_codec", "audio_codec", "method"),
    [("h264", "aac", "remux"), ("mpeg4", "aac", "transcode"), ("h264", "ac3", "transcode")],
)
def test_remote_real_hls_renewal_and_revocation(owner_context, real_ffmpeg, video_codec, audio_codec, method):
    from test_transcoding import conversion_file

    context, csrf = owner_context
    file_id, source, _library = conversion_file(context, csrf, real_ffmpeg, video_codec, audio_codec)
    media = RemoteMedia(context.config, context.session_factory, app.state.playback_manager)
    media.bind(str(uuid.uuid4()), str(uuid.uuid4()))
    auth = {
        "authorized": True,
        "agent_id": media.agent_id,
        "user_id": media.owner_id,
        "session_id": str(uuid.uuid4()),
        "role": "owner",
        "expires_at": time.time() + 800,
    }
    card = call(media, auth, "catalog.list")["items"][0]
    detail = call(media, auth, "catalog.detail", media_id=card["id"])
    assert detail["files"][0]["audio"]
    start = call(media, auth, "playback.start", file_id=card["file_id"], capabilities=CAPS)
    assert start["decision"]["method"] == method
    assert start["resource"] == "manifest"
    snapshot_id = str(uuid.uuid4())
    manifest = decode(
        call(media, auth, "playback.bytes", session_id=start["id"], resource="manifest", snapshot_id=snapshot_id)[
            "data"
        ]
    ).decode()
    assert "#EXTM3U" in manifest and source not in manifest and "http" not in manifest
    segment_id = next(line for line in manifest.splitlines() if line and not line.startswith("#"))
    assert uuid.UUID(segment_id).version == 4
    fresh = {**auth, "expires_at": time.time() + 899}
    chunk = call(media, fresh, "playback.bytes", session_id=start["id"], resource="segment", segment=segment_id)
    assert decode(chunk["data"])[0] == 0x47  # MPEG transport-stream sync byte
    with pytest.raises(HTTPException):
        call(media, fresh, "playback.bytes", session_id=start["id"], resource="segment", segment=str(uuid.uuid4()))
    with media.factory() as db:
        internal = media.resolve(db, "playback", start["id"])
    job = media.manager.for_session(internal)
    auth.update(expires_at=0, revoked=True)
    media.release(auth)
    assert not media.manifest_snapshots and not media.segments
    assert job.process.poll() is not None
    assert not job.directory.exists()
    with pytest.raises(HTTPException):
        call(media, fresh, "playback.bytes", session_id=start["id"], resource="segment", segment=segment_id)


def test_remote_real_subtitles_and_track_selection(owner_context, real_ffmpeg):
    from test_transcoding import conversion_file

    context, csrf = owner_context
    file_id, source, _library = conversion_file(context, csrf, real_ffmpeg)
    captions = context.media_root / "generated.srt"
    captions.write_text("1\n00:00:01,000 --> 00:00:03,000\nEncrypted synthetic cue.\n", encoding="utf-8")
    muxed = Path(source).with_name("captioned.mkv")
    subprocess.run(
        [
            real_ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            source,
            "-i",
            str(captions),
            "-map",
            "0:v:0",
            "-map",
            "0:a:0",
            "-map",
            "1:s:0",
            "-c",
            "copy",
            str(muxed),
        ],
        check=True,
        capture_output=True,
        timeout=15,
    )
    with context.session_factory() as db:
        file = db.get(MediaFile, file_id)
        file.relative_path, file.size_bytes, file.modified_ns = (
            muxed.name,
            muxed.stat().st_size,
            muxed.stat().st_mtime_ns,
        )
        db.add(SubtitleStream(media_file_id=file_id, stream_index=2, codec="subrip", language="eng"))
        db.commit()
    media = RemoteMedia(context.config, context.session_factory, app.state.playback_manager)
    media.bind(str(uuid.uuid4()), str(uuid.uuid4()))
    auth = {
        "authorized": True,
        "agent_id": media.agent_id,
        "user_id": media.owner_id,
        "session_id": str(uuid.uuid4()),
        "role": "owner",
        "expires_at": time.time() + 800,
    }
    card = call(media, auth, "catalog.list")["items"][0]
    details = call(media, auth, "catalog.detail", media_id=card["id"])
    assert details["files"][0]["subtitles"][0]["index"] == 2
    start = call(
        media, auth, "playback.start", file_id=card["file_id"], capabilities=CAPS, audio_index=1, subtitle_index=2
    )
    data = call(media, auth, "playback.bytes", session_id=start["id"], resource="subtitles")
    assert b"Encrypted synthetic cue." in decode(data["data"])
    assert str(context.media_root) not in decode(data["data"]).decode()
    call(media, auth, "playback.stop", session_id=start["id"])


def test_native_folder_consent_library_lifecycle_and_source_preservation(owner_context):
    context, _ = owner_context
    media, auth, _ = setup_remote(owner_context)
    context.config.deployment_mode = "native_windows"
    new_folder = context.media_root / "Selected"
    new_folder.mkdir()
    source = new_folder / "preserved.mp4"
    source.write_bytes(b"synthetic source preserved")
    request = {"id": str(uuid.uuid4()), "op": "folders.select", "payload": {}}
    with patch("app.native_consent.request_folder", new=AsyncMock(return_value=None)):
        assert not asyncio.run(media.dispatch(request, auth))["ok"]
    with patch("app.native_consent.request_folder", new=AsyncMock(return_value=new_folder)):
        approved = asyncio.run(media.dispatch(request, auth))
    assert approved["ok"], approved
    selection = approved["result"]["selection_id"]
    created = call(
        media, auth, "libraries.create", name="Locally selected", library_type="movies", selection_ids=[selection]
    )
    assert created["name"] == "Locally selected"
    assert len(created["paths"]) == 1
    started = call(media, auth, "libraries.scan", library_id=created["id"])
    assert started["status"] == "queued"
    assert call(media, auth, "libraries.status", library_id=created["id"])["status"] == "queued"
    assert call(media, auth, "libraries.stop", library_id=created["id"])["status"] == "cancelled"
    updated = call(
        media,
        auth,
        "libraries.update",
        library_id=created["id"],
        name="Updated",
        remove_path_ids=[created["paths"][0]["id"]],
    )
    assert updated["name"] == "Updated" and not updated["paths"][0]["enabled"]
    assert call(media, auth, "libraries.delete", library_id=created["id"])["removed"]
    assert source.read_bytes() == b"synthetic source preserved"


def test_remote_artwork_and_owner_progress_migration(owner_context):
    context, _ = owner_context
    media, auth, (library, media_id, file_id, source) = setup_remote(owner_context)
    with context.session_factory() as db:
        from sqlalchemy import select

        from app.models import Role, User

        original_owner = db.scalar(select(User).join(User.roles).where(Role.name == "Owner"))
        original_id = original_owner.id
        db.add(
            WatchProgress(
                user_id=original_id,
                media_item_id=media_id,
                media_file_id=file_id,
                position_seconds=25,
                duration_seconds=120,
            )
        )
        source.with_name("poster.jpg").write_bytes(b"\xff\xd8\xffsynthetic-jpeg")
        db.add(
            LocalArtwork(
                media_item_id=media_id,
                library_path_id=library["paths"][0]["id"],
                source_path="poster.jpg",
                artwork_type="poster",
                fingerprint="b" * 64,
            )
        )
        db.commit()
    card = call(media, auth, "catalog.list")["items"][0]
    assert card["position_seconds"] == 25 and card["artwork_id"]
    data = call(media, auth, "artwork.bytes", artwork_id=card["artwork_id"])
    assert decode(data["data"]) == b"\xff\xd8\xffsynthetic-jpeg"
    with context.session_factory() as db:
        assert db.get(PortalGrant, auth["user_id"]).local_user_id == original_id


def test_media_prefetch_does_not_consume_watch_progress_clock(owner_context):
    media, auth, _ = setup_remote(owner_context)
    card = call(media, auth, "catalog.list")["items"][0]
    started = call(media, auth, "playback.start", file_id=card["file_id"], capabilities=CAPS)
    call(
        media,
        auth,
        "playback.progress",
        session_id=started["id"],
        position_seconds=0,
        playing=True,
        reason="playing",
        sequence=1,
    )
    with media.factory() as db:
        internal = media.resolve(db, "playback", started["id"])
        row = db.get(PlaybackSession, internal)
        row.last_seen_at -= timedelta(seconds=5)
        db.commit()
        checkpoint = row.last_seen_at
    for offset in (0, 4096, 8192):
        call(media, auth, "playback.bytes", session_id=started["id"], resource="file", offset=offset, length=1024)
    with media.factory() as db:
        assert db.get(PlaybackSession, internal).last_seen_at == checkpoint.replace(tzinfo=None)
    call(
        media,
        auth,
        "playback.progress",
        session_id=started["id"],
        position_seconds=5,
        playing=True,
        reason="periodic",
        sequence=2,
    )
    detail = call(media, auth, "catalog.detail", media_id=card["id"])
    assert detail["position_seconds"] == 5
    with media.factory() as db:
        row = db.get(PlaybackSession, internal)
        assert row.watched_seconds >= 4.9
        credit = row.watched_seconds
    call(
        media,
        auth,
        "playback.progress",
        session_id=started["id"],
        position_seconds=100,
        playing=True,
        reason="seek",
        sequence=3,
    )
    with media.factory() as db:
        assert db.get(PlaybackSession, internal).watched_seconds == credit
    call(media, auth, "playback.stop", session_id=started["id"])
    resumed = call(media, auth, "playback.start", file_id=card["file_id"], capabilities=CAPS)
    assert resumed["position_seconds"] == 100


def test_job_aliases_and_sanitized_bounded_scan_errors(owner_context):
    media, auth, (_library, _media, file_id, _source) = setup_remote(owner_context)
    card = call(media, auth, "catalog.list")["items"][0]
    started = call(media, auth, "libraries.scan", library_id=card["library_id"])
    libraries = call(media, auth, "libraries.list")
    assert libraries["items"][0]["active_job_id"] == started["job_id"]
    with media.factory() as db:
        job_id = media.resolve(db, "job", started["job_id"])
        assert job_id != started["job_id"]
        db.get(BackgroundJob, job_id).error_summary = "C:/private-secret/synthetic.mkv failed"
        db.get(MediaFile, file_id).analysis_error = "C:/private-secret/synthetic.mkv failed"
        db.commit()
    status = call(media, auth, "libraries.status", library_id=card["library_id"])
    assert status["id"] == started["job_id"] and status["error_summary"]
    assert "private-secret" not in json.dumps(status)
    errors = call(media, auth, "libraries.errors", library_id=card["library_id"])
    assert errors["total"] == 1 and errors["page_size"] == 24
    assert errors["items"][0]["file_id"] == card["file_id"]
    assert errors["items"][0]["media_id"] == card["id"]
    assert "private-secret" not in json.dumps(errors)
    call(media, auth, "libraries.stop", library_id=card["library_id"])


def test_single_member_grant_lookup_is_independent_of_list_page(owner_context):
    media, auth, _ = setup_remote(owner_context)
    card = call(media, auth, "catalog.list")["items"][0]
    users = [str(uuid.uuid4()) for _ in range(30)]
    for user_id in users:
        call(
            media,
            auth,
            "grants.set",
            user_id=user_id,
            role="viewer",
            enabled=True,
            access_version=3,
            library_ids=[card["library_id"]],
        )
    first_page = {grant["user_id"] for grant in call(media, auth, "grants.list")["items"]}
    target = next(user_id for user_id in users if user_id not in first_page)
    result = call(media, auth, "grants.get", user_id=target)
    assert result == {
        "user_id": target,
        "role": "viewer",
        "enabled": True,
        "access_version": 3,
        "library_ids": [card["library_id"]],
    }
    missing = str(uuid.uuid4())
    assert call(media, auth, "grants.get", user_id=missing) == {"user_id": missing, "library_ids": []}


def test_single_member_grant_lookup_rejects_owner_and_cross_identity(owner_context):
    media, auth, _ = setup_remote(owner_context)
    card = call(media, auth, "catalog.list")["items"][0]
    target = str(uuid.uuid4())
    call(media, auth, "grants.set", user_id=target, role="viewer", library_ids=[card["library_id"]])
    with pytest.raises(HTTPException):
        call(media, auth, "grants.get", user_id=auth["user_id"])
    viewer = {**auth, "user_id": target, "session_id": str(uuid.uuid4()), "role": "viewer"}
    with pytest.raises(HTTPException):
        call(media, viewer, "grants.get", user_id=target)
    with pytest.raises(HTTPException):
        call(media, {**viewer, "role": "owner"}, "grants.get", user_id=target)
    with pytest.raises(HTTPException):
        call(media, {**auth, "agent_id": str(uuid.uuid4())}, "grants.get", user_id=target)
    with media.factory() as db:
        db.get(PortalGrant, target).agent_id = str(uuid.uuid4())
        db.commit()
    with pytest.raises(HTTPException) as denied:
        call(media, auth, "grants.get", user_id=target)
    assert denied.value.status_code == 404


def test_single_member_grant_lookup_never_truncates_existing_permissions(owner_context):
    media, auth, _ = setup_remote(owner_context)
    call(media, auth, "catalog.list")
    target = str(uuid.uuid4())
    call(media, auth, "grants.set", user_id=target, role="viewer", library_ids=[])
    with media.factory() as db:
        grant = db.get(PortalGrant, target)
        for _ in range(101):
            library = Library(name="Synthetic permission", library_type="movies")
            db.add(library)
            db.flush()
            db.add(UserLibrary(user_id=grant.local_user_id, library_id=library.id))
        db.commit()
    with pytest.raises(HTTPException) as denied:
        call(media, auth, "grants.get", user_id=target)
    assert denied.value.status_code == 409
