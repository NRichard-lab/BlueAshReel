from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import AppConfig, ApprovedMediaRoot, get_config
from app.database import get_db
from app.dependencies import (
    MediaBrowserAccess,
    Principal,
    require_media_browser,
    require_media_browser_csrf,
    require_owner,
)
from app.models import Library, LibraryPath
from app.schemas import (
    MediaFolderBrowseRequest,
    MediaFolderManualRequest,
    MediaFolderPage,
    MediaFolderSelectionPublic,
    MediaRootLibraryPublic,
    MediaRootList,
    MediaRootPublic,
)
from app.services.media_roots import (
    FolderPermissionDenied,
    FolderSelection,
    FolderUnavailable,
    InvalidFolderSelection,
    folder_state,
    list_folders,
    make_page_cursor,
    make_selection_id,
    read_only_enforced,
    resolve_page_cursor,
    resolve_selection_id,
    root_state,
    safe_display_name,
    selection_display_path,
    selection_from_path,
    validated_at,
)
from app.services.paths import UnsafeMediaPath

router = APIRouter()


def _owner(principal: Principal | None) -> bool:
    return principal is not None and "Owner" in {role.name for role in principal.user.roles}


def _library_usage(db: Session, config: AppConfig) -> dict[str, list[MediaRootLibraryPublic]]:
    usage: dict[str, dict[str, MediaRootLibraryPublic]] = {root.id: {} for root in config.approved_media_roots}
    rows = db.execute(
        select(Library.id, Library.name, LibraryPath.canonical_path).join(
            LibraryPath, LibraryPath.library_id == Library.id
        )
    )
    for library_id, library_name, raw_path in rows:
        candidate = Path(raw_path).resolve(strict=False)
        for root in config.approved_media_roots:
            try:
                candidate.relative_to(root.path.resolve(strict=False))
            except (OSError, RuntimeError, ValueError):
                continue
            usage[root.id][library_id] = MediaRootLibraryPublic(id=library_id, name=library_name)
            break
    return {
        root_id: sorted(items.values(), key=lambda item: (item.name.casefold(), item.name, item.id))
        for root_id, items in usage.items()
    }


def _root_public(
    root: ApprovedMediaRoot,
    config: AppConfig,
    *,
    include_internal: bool,
    libraries: list[MediaRootLibraryPublic] | None = None,
) -> MediaRootPublic:
    state, canonical = root_state(root)
    available = state == "available"
    return MediaRootPublic(
        id=root.id,
        display_name=root.display_name,
        selection_id=make_selection_id(root, (), config),
        available=available,
        readable=available,
        read_only=True,
        read_only_enforced=read_only_enforced(canonical) if canonical else None,
        status=state,
        internal_path=str(root.path) if include_internal else None,
        last_validated_at=validated_at(),
        libraries=libraries or [],
    )


def _selection_public(
    selection: FolderSelection,
    config: AppConfig,
    *,
    include_internal: bool,
) -> MediaFolderSelectionPublic:
    state = folder_state(selection.path)
    available = state == "available"
    return MediaFolderSelectionPublic(
        selection_id=make_selection_id(selection.root, selection.relative_parts, config),
        root_id=selection.root.id,
        name=(
            safe_display_name(selection.relative_parts[-1])
            if selection.relative_parts
            else selection.root.display_name
        ),
        display_path=selection_display_path(selection),
        internal_path=str(selection.path) if include_internal else None,
        available=available,
        readable=available,
        status=state,
        read_only=True,
    )


def _folder_error(exc: Exception) -> HTTPException:
    if isinstance(exc, FolderPermissionDenied):
        return HTTPException(
            status_code=409,
            detail={
                "message": "The service account cannot read the approved folder; check folder permissions",
                "state": "permission_denied",
            },
        )
    if isinstance(exc, FolderUnavailable):
        return HTTPException(
            status_code=409,
            detail={"message": f"{exc}; check that the drive or share is connected", "state": "unavailable"},
        )
    return HTTPException(status_code=422, detail=str(exc))


@router.get("/media-roots", response_model=MediaRootList, tags=["media storage"])
def list_media_roots(
    access: MediaBrowserAccess = Depends(require_media_browser),
    config: AppConfig = Depends(get_config),
) -> MediaRootList:
    include_internal = _owner(access.principal)
    return MediaRootList(
        platform="windows" if config.deployment_mode == "native_windows" else "docker",
        items=[
            _root_public(root, config, include_internal=include_internal)
            for root in config.approved_media_roots
        ]
    )


@router.get("/media-storage", response_model=MediaRootList, tags=["media storage"])
def media_storage(
    _principal: Principal = Depends(require_owner),
    db: Session = Depends(get_db),
    config: AppConfig = Depends(get_config),
) -> MediaRootList:
    usage = _library_usage(db, config)
    return MediaRootList(
        platform="windows" if config.deployment_mode == "native_windows" else "docker",
        items=[
            _root_public(root, config, include_internal=True, libraries=usage[root.id])
            for root in config.approved_media_roots
        ]
    )


@router.post("/media-folders/browse", response_model=MediaFolderPage, tags=["media storage"])
def browse_media_folders(
    payload: MediaFolderBrowseRequest,
    access: MediaBrowserAccess = Depends(require_media_browser_csrf),
    config: AppConfig = Depends(get_config),
) -> MediaFolderPage:
    try:
        current = resolve_selection_id(payload.selection_id, config)
        offset = resolve_page_cursor(payload.cursor, payload.selection_id, config)
        folders = list_folders(current)
    except (InvalidFolderSelection, FolderPermissionDenied, FolderUnavailable) as exc:
        raise _folder_error(exc) from exc

    end = min(len(folders), offset + payload.page_size)
    include_internal = _owner(access.principal)
    breadcrumbs = [
        FolderSelection(
            root=current.root,
            path=current.root.path.joinpath(*current.relative_parts[:index]),
            relative_parts=current.relative_parts[:index],
        )
        for index in range(len(current.relative_parts) + 1)
    ]
    return MediaFolderPage(
        platform="windows" if config.deployment_mode == "native_windows" else "docker",
        root=_root_public(current.root, config, include_internal=include_internal),
        current=_selection_public(current, config, include_internal=include_internal),
        breadcrumbs=[
            _selection_public(selection, config, include_internal=include_internal) for selection in breadcrumbs
        ],
        items=[
            _selection_public(selection, config, include_internal=include_internal)
            for selection in folders[offset:end]
        ],
        next_cursor=(
            make_page_cursor(payload.selection_id, end, config) if end < len(folders) else None
        ),
        total=len(folders),
    )


@router.post("/media-folders/validate", response_model=MediaFolderSelectionPublic, tags=["media storage"])
def validate_manual_media_folder(
    payload: MediaFolderManualRequest,
    access: MediaBrowserAccess = Depends(require_media_browser_csrf),
    config: AppConfig = Depends(get_config),
) -> MediaFolderSelectionPublic:
    try:
        selection = selection_from_path(payload.path, config)
    except UnsafeMediaPath as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _selection_public(selection, config, include_internal=_owner(access.principal))
