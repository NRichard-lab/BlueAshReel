from __future__ import annotations

from pathlib import Path
from shutil import disk_usage
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import distinct, func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.config import AppConfig, ProductConfig, get_config, get_product_config
from app.database import database_is_ready, get_db
from app.dependencies import (
    Principal,
    current_principal,
    require_csrf,
    require_manager,
    require_manager_csrf,
    require_owner,
    require_user_csrf,
)
from app.models import (
    ApplicationSetting,
    AuditEvent,
    BackgroundJob,
    Library,
    LibraryPath,
    MediaFile,
    MediaItem,
    Role,
    ScanJob,
    ScanLock,
    User,
    UserLibrary,
    utcnow,
)
from app.schemas import (
    AuditList,
    AuditPublic,
    AuthResponse,
    DashboardPublic,
    InitialLibrary,
    JobAccepted,
    JobList,
    JobPublic,
    LibraryCreate,
    LibraryList,
    LibraryPathCreate,
    LibraryPathPublic,
    LibraryPublic,
    LibraryUpdate,
    LoginRequest,
    MediaFileSummary,
    MediaList,
    MediaPublic,
    OwnerSetupRequest,
    PrivacyPublic,
    PrivacyUpdate,
    ProductPublic,
    ScanRequest,
    SettingsPublic,
    SettingUpdate,
    SetupResponse,
    SetupStatus,
    UserPublic,
)
from app.security import create_session, hash_password, normalize_username, verify_login
from app.services.audit import record_audit
from app.services.catalog import permitted_library
from app.services.ffprobe import ffmpeg_available, ffprobe_available
from app.services.jobs import ScanAlreadyRunning, enqueue_scan, release_scan_lock
from app.services.media_state import recompute_media_availability
from app.services.outbound import KNOWN_INTEGRATIONS, outbound_enabled
from app.services.paths import UnsafeMediaPath, validate_media_directory
from app.services.rate_limit import login_attempt_key, login_host_key, login_rate_limiter

router = APIRouter()
CSRF_COOKIE_NAME = "csrf_token"
Page = Annotated[int, Query(ge=1, le=1_000_000)]
PageSize = Annotated[int, Query(ge=1, le=100)]


def _product_public(product: ProductConfig) -> ProductPublic:
    return ProductPublic.model_validate(product.model_dump())


def _owner_exists(db: Session) -> bool:
    return (
        db.scalar(select(func.count(User.id)).select_from(User).join(User.roles).where(Role.name == "Owner")) or 0
    ) > 0


def _user_public(user: User) -> UserPublic:
    return UserPublic(id=user.id, username=user.username, roles=sorted(role.name for role in user.roles))


def _set_session_cookies(response: Response, token: str, csrf_token: str, config: AppConfig) -> None:
    response.set_cookie(
        key=config.session_cookie_name,
        value=token,
        max_age=config.session_ttl_hours * 3600,
        httponly=True,
        secure=config.session_cookie_secure,
        samesite="strict",
        path="/",
    )
    response.set_cookie(
        key=CSRF_COOKIE_NAME,
        value=csrf_token,
        max_age=config.session_ttl_hours * 3600,
        httponly=False,
        secure=config.session_cookie_secure,
        samesite="strict",
        path="/",
    )


def _safe_setup_directory(raw: str | None, configured: Path) -> Path:
    expected = configured.expanduser().resolve()
    candidate = Path(raw).expanduser().resolve() if raw else expected
    if candidate != expected:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Setup directories must match the server's configured persistent mounts",
        )
    candidate.mkdir(parents=True, exist_ok=True)
    if not candidate.is_dir():
        raise HTTPException(status_code=422, detail="Configured storage location is not a directory")
    return candidate


def _validated_paths(paths: list[str], config: AppConfig, *, disclose_reason: bool = True) -> list[Path]:
    try:
        resolved = [validate_media_directory(item, config) for item in paths]
    except UnsafeMediaPath as exc:
        detail = str(exc) if disclose_reason else "Initial media directory is unavailable or unsafe"
        raise HTTPException(status_code=422, detail=detail) from exc
    if len({str(item) for item in resolved}) != len(resolved):
        raise HTTPException(status_code=422, detail="Duplicate media directories are not allowed")
    return resolved


def _create_library(db: Session, payload: LibraryCreate | InitialLibrary, config: AppConfig) -> Library:
    resolved = _validated_paths(payload.paths, config)
    library = Library(
        name=payload.name.strip(),
        library_type=payload.library_type,
        enabled=getattr(payload, "enabled", True),
    )
    db.add(library)
    db.flush()
    for path in resolved:
        db.add(LibraryPath(library_id=library.id, canonical_path=str(path)))
    db.flush()
    db.refresh(library)
    return library


@router.get("/config/public", response_model=ProductPublic, tags=["public"])
def public_config(product: ProductConfig = Depends(get_product_config)) -> ProductPublic:
    return _product_public(product)


@router.get("/setup/status", response_model=SetupStatus, tags=["setup"])
def setup_status(db: Session = Depends(get_db), product: ProductConfig = Depends(get_product_config)) -> SetupStatus:
    return SetupStatus(setup_required=not _owner_exists(db), product=_product_public(product))


@router.post("/setup/owner", response_model=SetupResponse, status_code=201, tags=["setup"])
def setup_owner(
    payload: OwnerSetupRequest,
    response: Response,
    db: Session = Depends(get_db),
    config: AppConfig = Depends(get_config),
) -> SetupResponse:
    # Avoid expensive password hashing or path work after setup, while retaining the
    # serialized check below to close the concurrent first-run race.
    if _owner_exists(db):
        raise HTTPException(status_code=409, detail="Initial setup is already complete")
    app_data = _safe_setup_directory(payload.application_data_directory, config.app_data_dir)
    temp = _safe_setup_directory(payload.temporary_directory, config.temp_dir)
    config.artwork_dir.expanduser().resolve().mkdir(parents=True, exist_ok=True)
    initial_paths = (
        _validated_paths(payload.initial_library.paths, config, disclose_reason=False)
        if payload.initial_library
        else []
    )
    password_hash = hash_password(payload.password)
    try:
        if db.get_bind().dialect.name == "sqlite":
            db.execute(text("BEGIN IMMEDIATE"))
        if _owner_exists(db):
            db.rollback()
            raise HTTPException(status_code=409, detail="Initial setup is already complete")
        roles: dict[str, Role] = {}
        for role_name in ("Owner", "Administrator", "Viewer"):
            role = db.scalar(select(Role).where(Role.name == role_name))
            if role is None:
                role = Role(name=role_name)
                db.add(role)
                db.flush()
            roles[role_name] = role
        user = User(
            username=payload.username.strip(),
            normalized_username=normalize_username(payload.username),
            password_hash=password_hash,
            roles=[roles["Owner"]],
        )
        db.add(user)
        db.flush()
        for key, value in {
            "storage.application_data": str(app_data),
            "storage.temporary": str(temp),
            "storage.artwork": str(config.artwork_dir.expanduser().resolve()),
            "privacy.local_only": True,
        }.items():
            db.merge(ApplicationSetting(key=key, value=value))
        if payload.initial_library:
            library = Library(
                name=payload.initial_library.name.strip(),
                library_type=payload.initial_library.library_type,
                enabled=True,
            )
            db.add(library)
            db.flush()
            for path in initial_paths:
                db.add(LibraryPath(library_id=library.id, canonical_path=str(path)))
            db.add(UserLibrary(user_id=user.id, library_id=library.id))
        new_session = create_session(db, user, config)
        record_audit(db, "owner.setup_completed", actor_user_id=user.id, target_type="user", target_id=user.id)
        db.commit()
        db.refresh(user)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Initial setup could not be completed") from exc
    _set_session_cookies(response, new_session.token, new_session.csrf_token, config)
    return SetupResponse(
        user=_user_public(user),
        csrf_token=new_session.csrf_token,
        health={
            "database": database_is_ready(db.get_bind()),
            "application_data": app_data.is_dir(),
            "temporary_storage": temp.is_dir(),
            "ffprobe": ffprobe_available(config.ffprobe_path),
            "ffmpeg": ffmpeg_available(config.ffmpeg_path),
        },
    )


@router.post("/auth/login", response_model=AuthResponse, tags=["authentication"])
def login(
    payload: LoginRequest,
    response: Response,
    request: Request,
    db: Session = Depends(get_db),
    config: AppConfig = Depends(get_config),
) -> AuthResponse:
    # Use the transport peer supplied by the ASGI server. Forwarding headers are
    # intentionally ignored because this local deployment does not establish a
    # trusted-proxy chain and attacker-controlled X-Forwarded-For values must not
    # reset the host-wide failure budget.
    client_host = request.client.host if request.client else "unknown"
    attempt_key = login_attempt_key(config.app_secret_key, payload.username, client_host)
    host_key = login_host_key(config.app_secret_key, client_host)
    retry_delays = [
        delay for key in (attempt_key, host_key) if (delay := login_rate_limiter.retry_after(key)) is not None
    ]
    retry_after = max(retry_delays, default=None)
    if retry_after is not None:
        record_audit(db, "auth.login", outcome="throttled")
        db.commit()
        raise HTTPException(
            status_code=429,
            detail="Too many login attempts; try again later",
            headers={"Retry-After": str(retry_after)},
        )
    user = verify_login(db, payload.username, payload.password)
    if user is None:
        login_rate_limiter.record_failure(attempt_key)
        login_rate_limiter.record_failure(host_key)
        record_audit(db, "auth.login", outcome="denied")
        db.commit()
        raise HTTPException(status_code=401, detail="Invalid username or password")
    login_rate_limiter.record_success(attempt_key)
    login_rate_limiter.record_success(host_key)
    session = create_session(db, user, config)
    record_audit(db, "auth.login", actor_user_id=user.id)
    db.commit()
    _set_session_cookies(response, session.token, session.csrf_token, config)
    return AuthResponse(user=_user_public(user), csrf_token=session.csrf_token)


@router.get("/auth/me", response_model=UserPublic, tags=["authentication"])
def me(principal: Principal = Depends(current_principal)) -> UserPublic:
    return _user_public(principal.user)


@router.post("/auth/logout", status_code=204, tags=["authentication"])
def logout(
    response: Response,
    principal: Principal = Depends(require_user_csrf),
    db: Session = Depends(get_db),
    config: AppConfig = Depends(get_config),
) -> None:
    from app.services.playback_lifecycle import end_sessions

    end_sessions(db, auth_id=principal.session.id)
    principal.session.revoked_at = utcnow()
    record_audit(db, "auth.logout", actor_user_id=principal.user.id)
    db.commit()
    response.delete_cookie(config.session_cookie_name, path="/", samesite="strict")
    response.delete_cookie(CSRF_COOKIE_NAME, path="/", samesite="strict")


def _active_jobs_by_library(db: Session, library_ids: list[str]) -> dict[str, str]:
    if not library_ids:
        return {}
    rows = db.execute(
        select(ScanJob.library_id, BackgroundJob.id)
        .join(BackgroundJob, BackgroundJob.id == ScanJob.job_id)
        .where(
            ScanJob.library_id.in_(library_ids),
            BackgroundJob.status.in_(("queued", "running", "retry_wait")),
        )
    ).all()
    return {library_id: job_id for library_id, job_id in rows}


def _ensure_library_not_scanning(db: Session, library_id: str) -> None:
    lock = db.get(ScanLock, library_id)
    if lock is None:
        return
    job = db.get(BackgroundJob, lock.job_id)
    if job is None or job.status not in {"queued", "running", "retry_wait"}:
        db.delete(lock)
        db.flush()
        return
    raise HTTPException(
        status_code=409,
        detail={
            "message": "Library configuration cannot change while a scan is active",
            "existing_job_id": job.id,
        },
    )


def _ensure_no_active_scans(db: Session) -> None:
    active = db.execute(
        select(ScanLock.job_id, BackgroundJob.status)
        .join(BackgroundJob, BackgroundJob.id == ScanLock.job_id)
        .where(BackgroundJob.status.in_(("queued", "running", "retry_wait")))
        .limit(1)
    ).first()
    if active:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Scanner settings cannot change while a scan is active",
                "existing_job_id": active.job_id,
            },
        )


def _library_page(db: Session, page: int, page_size: int) -> LibraryList:
    total = db.scalar(select(func.count()).select_from(Library)) or 0
    libraries = db.scalars(
        select(Library)
        .options(selectinload(Library.paths))
        .order_by(Library.name.asc(), Library.id.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    ids = [library.id for library in libraries]
    counts = {
        library_id: (media_count, file_count, error_count)
        for library_id, media_count, file_count, error_count in db.execute(
            select(
                Library.id,
                func.count(distinct(MediaItem.id)),
                func.count(distinct(MediaFile.id)).filter(MediaFile.available.is_(True)),
                func.count(distinct(MediaFile.id)).filter(MediaFile.analysis_error.is_not(None)),
            )
            .outerjoin(MediaItem, MediaItem.library_id == Library.id)
            .outerjoin(MediaFile, MediaFile.media_item_id == MediaItem.id)
            .where(Library.id.in_(ids))
            .group_by(Library.id)
        ).all()
    }
    active = _active_jobs_by_library(db, ids)
    items = []
    for library in libraries:
        media_count, file_count, error_count = counts.get(library.id, (0, 0, 0))
        items.append(
            LibraryPublic(
                id=library.id,
                name=library.name,
                library_type=library.library_type,
                enabled=library.enabled,
                paths=[
                    LibraryPathPublic(id=item.id, path=f"Media folder {item.id[:8]}", enabled=item.enabled)
                    for item in library.paths
                ],
                last_successful_scan_at=library.last_successful_scan_at,
                media_count=media_count,
                available_file_count=file_count,
                error_count=error_count,
                active_job_id=active.get(library.id),
            )
        )
    return LibraryList(items=items, total=total, page=page, page_size=page_size)


def _library_by_id(db: Session, library_id: str) -> LibraryPublic | None:
    library = db.scalar(select(Library).options(selectinload(Library.paths)).where(Library.id == library_id))
    if library is None:
        return None
    media_count, file_count, error_count = db.execute(
        select(
            func.count(distinct(MediaItem.id)),
            func.count(distinct(MediaFile.id)).filter(MediaFile.available.is_(True)),
            func.count(distinct(MediaFile.id)).filter(MediaFile.analysis_error.is_not(None)),
        )
        .select_from(Library)
        .outerjoin(MediaItem, MediaItem.library_id == Library.id)
        .outerjoin(MediaFile, MediaFile.media_item_id == MediaItem.id)
        .where(Library.id == library_id)
    ).one()
    return LibraryPublic(
        id=library.id,
        name=library.name,
        library_type=library.library_type,
        enabled=library.enabled,
        paths=[
            LibraryPathPublic(id=item.id, path=f"Media folder {item.id[:8]}", enabled=item.enabled)
            for item in library.paths
        ],
        last_successful_scan_at=library.last_successful_scan_at,
        media_count=media_count,
        available_file_count=file_count,
        error_count=error_count,
        active_job_id=_active_jobs_by_library(db, [library_id]).get(library_id),
    )


@router.get("/libraries", response_model=LibraryList, tags=["libraries"])
def list_libraries(
    page: Page = 1,
    page_size: PageSize = 25,
    _principal: Principal = Depends(require_manager),
    db: Session = Depends(get_db),
) -> LibraryList:
    return _library_page(db, page, page_size)


@router.post("/libraries", response_model=LibraryPublic, status_code=201, tags=["libraries"])
def create_library(
    payload: LibraryCreate,
    principal: Principal = Depends(require_manager_csrf),
    db: Session = Depends(get_db),
    config: AppConfig = Depends(get_config),
) -> LibraryPublic:
    library = _create_library(db, payload, config)
    db.add(UserLibrary(user_id=principal.user.id, library_id=library.id))
    record_audit(
        db,
        "library.created",
        actor_user_id=principal.user.id,
        target_type="library",
        target_id=library.id,
    )
    db.commit()
    result = _library_by_id(db, library.id)
    assert result is not None
    return result


@router.get("/libraries/{library_id}", response_model=LibraryPublic, tags=["libraries"])
def get_library(
    library_id: str,
    _principal: Principal = Depends(require_manager),
    db: Session = Depends(get_db),
) -> LibraryPublic:
    item = _library_by_id(db, library_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Library not found")
    return item


@router.patch("/libraries/{library_id}", response_model=LibraryPublic, tags=["libraries"])
def update_library(
    library_id: str,
    payload: LibraryUpdate,
    principal: Principal = Depends(require_manager_csrf),
    db: Session = Depends(get_db),
) -> LibraryPublic:
    library = db.get(Library, library_id)
    if library is None:
        raise HTTPException(status_code=404, detail="Library not found")
    _ensure_library_not_scanning(db, library_id)
    if payload.name is not None:
        library.name = payload.name.strip()
    if payload.enabled is not None:
        library.enabled = payload.enabled
        if not payload.enabled:
            now = utcnow()
            library_path_ids = select(LibraryPath.id).where(LibraryPath.library_id == library.id)
            db.execute(
                update(MediaFile)
                .where(
                    MediaFile.library_path_id.in_(library_path_ids),
                    MediaFile.available.is_(True),
                )
                .values(available=False, missing_since=now)
                .execution_options(synchronize_session=False)
            )
            db.execute(
                update(MediaItem)
                .where(MediaItem.library_id == library.id)
                .values(available=False)
                .execution_options(synchronize_session=False)
            )
    record_audit(
        db,
        "library.updated",
        actor_user_id=principal.user.id,
        target_type="library",
        target_id=library.id,
    )
    db.commit()
    result = _library_by_id(db, library.id)
    assert result is not None
    return result


@router.post("/libraries/{library_id}/paths", response_model=LibraryPathPublic, status_code=201, tags=["libraries"])
def add_library_path(
    library_id: str,
    payload: LibraryPathCreate,
    principal: Principal = Depends(require_manager_csrf),
    db: Session = Depends(get_db),
    config: AppConfig = Depends(get_config),
) -> LibraryPathPublic:
    if db.get(Library, library_id) is None:
        raise HTTPException(status_code=404, detail="Library not found")
    _ensure_library_not_scanning(db, library_id)
    path = _validated_paths([payload.path], config)[0]
    existing = db.scalar(
        select(LibraryPath).where(LibraryPath.library_id == library_id, LibraryPath.canonical_path == str(path))
    )
    if existing:
        existing.enabled = True
        library_path = existing
    else:
        library_path = LibraryPath(library_id=library_id, canonical_path=str(path))
        db.add(library_path)
        db.flush()
    record_audit(
        db,
        "library.path_added",
        actor_user_id=principal.user.id,
        target_type="library",
        target_id=library_id,
    )
    db.commit()
    return LibraryPathPublic(id=library_path.id, path=f"Media folder {library_path.id[:8]}", enabled=True)


@router.delete("/libraries/{library_id}/paths/{path_id}", status_code=204, tags=["libraries"])
def remove_library_path(
    library_id: str,
    path_id: str,
    principal: Principal = Depends(require_manager_csrf),
    db: Session = Depends(get_db),
) -> None:
    library_path = db.scalar(select(LibraryPath).where(LibraryPath.id == path_id, LibraryPath.library_id == library_id))
    if library_path is None:
        raise HTTPException(status_code=404, detail="Library path not found")
    _ensure_library_not_scanning(db, library_id)
    library_path.enabled = False
    db.execute(
        update(MediaFile)
        .where(MediaFile.library_path_id == library_path.id, MediaFile.available.is_(True))
        .values(available=False, missing_since=utcnow())
        .execution_options(synchronize_session=False)
    )
    recompute_media_availability(db, library_id)
    record_audit(
        db,
        "library.path_disabled",
        actor_user_id=principal.user.id,
        target_type="library",
        target_id=library_id,
    )
    db.commit()


@router.post("/libraries/{library_id}/scans", response_model=JobAccepted, status_code=202, tags=["libraries"])
def start_scan(
    library_id: str,
    payload: ScanRequest,
    principal: Principal = Depends(require_manager_csrf),
    db: Session = Depends(get_db),
) -> JobAccepted:
    library = db.get(Library, library_id)
    if library is None:
        raise HTTPException(status_code=404, detail="Library not found")
    if not library.enabled:
        raise HTTPException(status_code=409, detail="Library is disabled")
    if not any(item.enabled for item in library.paths):
        raise HTTPException(status_code=409, detail="Library has no enabled media directories")
    try:
        job = enqueue_scan(db, library_id, payload.mode)
    except ScanAlreadyRunning as exc:
        raise HTTPException(
            status_code=409,
            detail={"message": "A scan is already active", "existing_job_id": exc.job_id},
        ) from exc
    record_audit(
        db,
        "library.scan_queued",
        actor_user_id=principal.user.id,
        target_type="library",
        target_id=library.id,
        details={"mode": payload.mode, "job_id": job.id},
    )
    db.commit()
    return JobAccepted(job_id=job.id, status=job.status)


def _job_public(job: BackgroundJob) -> JobPublic:
    scan: dict[str, Any] | None = None
    if job.scan:
        scan = {
            "library_id": job.scan.library_id,
            "mode": job.scan.scan_mode,
            "discovered_files": job.scan.discovered_files,
            "processed_files": job.scan.processed_files,
            "unchanged_files": job.scan.unchanged_files,
            "missing_files": job.scan.missing_files,
            "error_count": job.scan.error_count,
            "duration_ms": job.scan.duration_ms,
        }
    return JobPublic(
        id=job.id,
        job_type=job.job_type,
        status=job.status,
        progress_current=job.progress_current,
        progress_total=job.progress_total,
        attempts=job.attempts,
        max_attempts=job.max_attempts,
        cancel_requested=job.cancel_requested,
        error_summary=job.error_summary,
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        scan=scan,
    )


@router.get("/jobs", response_model=JobList, tags=["jobs"])
def list_jobs(
    page: Page = 1,
    page_size: PageSize = 25,
    job_status: str | None = Query(default=None, alias="status"),
    library_id: str | None = None,
    _principal: Principal = Depends(require_manager),
    db: Session = Depends(get_db),
) -> JobList:
    filters = [BackgroundJob.status == job_status] if job_status else []
    if library_id:
        filters.append(BackgroundJob.id.in_(select(ScanJob.job_id).where(ScanJob.library_id == library_id)))
    total = db.scalar(select(func.count()).select_from(BackgroundJob).where(*filters)) or 0
    jobs = db.scalars(
        select(BackgroundJob)
        .options(selectinload(BackgroundJob.scan))
        .where(*filters)
        .order_by(BackgroundJob.created_at.desc(), BackgroundJob.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return JobList(items=[_job_public(job) for job in jobs], total=total, page=page, page_size=page_size)


@router.get("/jobs/{job_id}", response_model=JobPublic, tags=["jobs"])
def get_job(
    job_id: str,
    _principal: Principal = Depends(require_manager),
    db: Session = Depends(get_db),
) -> JobPublic:
    job = db.scalar(select(BackgroundJob).options(selectinload(BackgroundJob.scan)).where(BackgroundJob.id == job_id))
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return _job_public(job)


@router.post("/jobs/{job_id}/cancel", response_model=JobPublic, tags=["jobs"])
def cancel_job(
    job_id: str,
    principal: Principal = Depends(require_manager_csrf),
    db: Session = Depends(get_db),
) -> JobPublic:
    job = db.scalar(select(BackgroundJob).options(selectinload(BackgroundJob.scan)).where(BackgroundJob.id == job_id))
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status not in {"queued", "running", "retry_wait"}:
        raise HTTPException(status_code=409, detail="Job is already finished")
    job.cancel_requested = True
    if job.status in {"queued", "retry_wait"}:
        job.status = "cancelled"
        job.completed_at = utcnow()
        release_scan_lock(db, job.id)
    record_audit(
        db,
        "job.cancel_requested",
        actor_user_id=principal.user.id,
        target_type="job",
        target_id=job.id,
    )
    db.commit()
    return _job_public(job)


def _media_public(item: MediaItem) -> MediaPublic:
    return MediaPublic(
        id=item.id,
        library_id=item.library_id,
        kind=item.kind,
        title=item.title,
        year=item.year,
        match_confidence=item.match_confidence,
        available=item.available,
        files=[
            MediaFileSummary(
                id=media_file.id,
                available=media_file.available,
                container=media_file.container,
                duration_seconds=media_file.duration_seconds,
                analysis_error=media_file.analysis_error,
                video_streams=len(media_file.video_streams),
                audio_streams=len(media_file.audio_streams),
                subtitle_streams=len(media_file.subtitle_streams),
                video=[
                    {
                        "index": stream.stream_index,
                        "codec": stream.codec,
                        "width": stream.width,
                        "height": stream.height,
                        "bitrate": stream.bitrate,
                        "frame_rate": stream.frame_rate,
                        "language": stream.language,
                    }
                    for stream in media_file.video_streams
                ],
                audio=[
                    {
                        "index": stream.stream_index,
                        "codec": stream.codec,
                        "channels": stream.channels,
                        "channel_layout": stream.channel_layout,
                        "bitrate": stream.bitrate,
                        "language": stream.language,
                        "title": stream.title,
                    }
                    for stream in media_file.audio_streams
                ],
                subtitles=[
                    {
                        "index": stream.stream_index,
                        "codec": stream.codec,
                        "language": stream.language,
                        "title": stream.title,
                        "forced": stream.forced,
                        "hearing_impaired": stream.hearing_impaired,
                    }
                    for stream in media_file.subtitle_streams
                ],
            )
            for media_file in item.files
        ],
    )


@router.get("/media", response_model=MediaList, tags=["media"])
def list_media(
    page: Page = 1,
    page_size: PageSize = 25,
    library_id: str | None = None,
    kind: str | None = None,
    available: bool | None = True,
    search: str | None = Query(default=None, max_length=200),
    _principal: Principal = Depends(require_manager),
    db: Session = Depends(get_db),
) -> MediaList:
    filters: list[Any] = [permitted_library(_principal.user.id)]
    if library_id:
        filters.append(MediaItem.library_id == library_id)
    if kind:
        filters.append(MediaItem.kind == kind)
    if available is not None:
        filters.append(MediaItem.available == available)
    if search:
        filters.append(MediaItem.title.contains(search.strip(), autoescape=True))
    total = db.scalar(select(func.count()).select_from(MediaItem).where(*filters)) or 0
    items = db.scalars(
        select(MediaItem)
        .options(
            selectinload(MediaItem.files).selectinload(MediaFile.video_streams),
            selectinload(MediaItem.files).selectinload(MediaFile.audio_streams),
            selectinload(MediaItem.files).selectinload(MediaFile.subtitle_streams),
        )
        .where(*filters)
        .order_by(MediaItem.sort_title.asc(), MediaItem.id.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return MediaList(items=[_media_public(item) for item in items], total=total, page=page, page_size=page_size)


@router.get("/media/{media_id}", response_model=MediaPublic, tags=["media"])
def get_media(
    media_id: str,
    _principal: Principal = Depends(require_manager),
    db: Session = Depends(get_db),
) -> MediaPublic:
    item = db.scalar(
        select(MediaItem)
        .options(
            selectinload(MediaItem.files).selectinload(MediaFile.video_streams),
            selectinload(MediaItem.files).selectinload(MediaFile.audio_streams),
            selectinload(MediaItem.files).selectinload(MediaFile.subtitle_streams),
        )
        .where(MediaItem.id == media_id, permitted_library(_principal.user.id))
    )
    if item is None:
        raise HTTPException(status_code=404, detail="Media item not found")
    return _media_public(item)


def _setting_value(db: Session, key: str, default: Any) -> Any:
    setting = db.get(ApplicationSetting, key)
    return setting.value if setting else default


@router.get("/settings", response_model=SettingsPublic, tags=["settings"])
def get_settings(
    _principal: Principal = Depends(require_owner),
    db: Session = Depends(get_db),
    config: AppConfig = Depends(get_config),
) -> SettingsPublic:
    return SettingsPublic(
        application_data_directory="Managed in server configuration",
        temporary_directory="Managed in server configuration",
        artwork_directory="Managed in server configuration",
        scan_extensions=list(_setting_value(db, "scanner.extensions", sorted(config.supported_extensions))),
        ignored_directories=list(
            _setting_value(db, "scanner.ignored_directories", sorted(config.ignored_directory_names))
        ),
    )


@router.patch("/settings", response_model=SettingsPublic, tags=["settings"])
def update_settings(
    payload: SettingUpdate,
    principal: Principal = Depends(require_csrf),
    db: Session = Depends(get_db),
    config: AppConfig = Depends(get_config),
) -> SettingsPublic:
    _ensure_no_active_scans(db)
    if payload.scan_extensions is not None:
        normalized = sorted(
            {
                item.strip().casefold() if item.strip().startswith(".") else f".{item.strip().casefold()}"
                for item in payload.scan_extensions
                if item.strip()
            }
        )
        db.merge(ApplicationSetting(key="scanner.extensions", value=normalized))
    if payload.ignored_directories is not None:
        ignored = sorted({item.strip() for item in payload.ignored_directories if item.strip()})
        db.merge(ApplicationSetting(key="scanner.ignored_directories", value=ignored))
    record_audit(db, "settings.updated", actor_user_id=principal.user.id)
    db.commit()
    return get_settings(principal, db, config)


def _privacy_public(db: Session, config: AppConfig) -> PrivacyPublic:
    return PrivacyPublic(
        runtime_outbound_allowed=config.outbound_integrations_enabled,
        integrations={name: outbound_enabled(db, config, name) for name in KNOWN_INTEGRATIONS},
    )


@router.get("/privacy", response_model=PrivacyPublic, tags=["privacy"])
def get_privacy(
    _principal: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
    config: AppConfig = Depends(get_config),
) -> PrivacyPublic:
    return _privacy_public(db, config)


@router.patch("/privacy", response_model=PrivacyPublic, tags=["privacy"])
def update_privacy(
    payload: PrivacyUpdate,
    principal: Principal = Depends(require_csrf),
    db: Session = Depends(get_db),
    config: AppConfig = Depends(get_config),
) -> PrivacyPublic:
    unknown = sorted(set(payload.integrations) - set(KNOWN_INTEGRATIONS))
    if unknown:
        raise HTTPException(status_code=422, detail="Unknown outbound integration")
    if not config.outbound_integrations_enabled and any(payload.integrations.values()):
        raise HTTPException(
            status_code=409,
            detail=("Outbound integrations are disabled by the server runtime; no latent enablement was stored"),
        )
    for name, enabled in payload.integrations.items():
        db.merge(ApplicationSetting(key=f"outbound.{name}.enabled", value=enabled))
    record_audit(
        db,
        "privacy.updated",
        actor_user_id=principal.user.id,
        details={"integrations_changed": sorted(payload.integrations)},
    )
    db.commit()
    return _privacy_public(db, config)


@router.get("/dashboard", response_model=DashboardPublic, tags=["system"])
def dashboard(
    _principal: Principal = Depends(require_manager),
    db: Session = Depends(get_db),
    config: AppConfig = Depends(get_config),
) -> DashboardPublic:
    last_scan = db.scalar(select(func.max(Library.last_successful_scan_at)))

    def storage_detail(path: Path) -> dict[str, Any]:
        resolved = path.expanduser().resolve()
        ready = resolved.is_dir()
        try:
            free_bytes: int | None = disk_usage(resolved).free if ready else None
        except OSError:
            free_bytes = None
            ready = False
        return {"configured_path": "Managed in server configuration", "ready": ready, "free_bytes": free_bytes}

    return DashboardPublic(
        server_status="ok",
        database_status="ok" if database_is_ready(db.get_bind()) else "unavailable",
        ffprobe_available=ffprobe_available(config.ffprobe_path),
        ffmpeg_available=ffmpeg_available(config.ffmpeg_path),
        library_count=db.scalar(select(func.count()).select_from(Library)) or 0,
        media_count=db.scalar(select(func.count()).select_from(MediaItem)) or 0,
        active_job_count=db.scalar(
            select(func.count())
            .select_from(BackgroundJob)
            .where(BackgroundJob.status.in_(("queued", "running", "retry_wait")))
        )
        or 0,
        last_scan_at=last_scan,
        storage={
            "application_data_configured": config.app_data_dir.is_dir(),
            "temporary_storage_configured": config.temp_dir.is_dir(),
            "artwork_storage_configured": config.artwork_dir.is_dir(),
        },
        storage_details={
            "application_data": storage_detail(config.app_data_dir),
            "temporary": storage_detail(config.temp_dir),
            "artwork": storage_detail(config.artwork_dir),
        },
    )


@router.get("/audit-events", response_model=AuditList, tags=["audit"])
def audit_events(
    page: Page = 1,
    page_size: PageSize = 25,
    _principal: Principal = Depends(require_owner),
    db: Session = Depends(get_db),
) -> AuditList:
    total = db.scalar(select(func.count()).select_from(AuditEvent)) or 0
    events = db.scalars(
        select(AuditEvent)
        .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return AuditList(
        items=[
            AuditPublic(
                id=event.id,
                event_type=event.event_type,
                target_type=event.target_type,
                target_id=event.target_id,
                outcome=event.outcome,
                details=event.details,
                created_at=event.created_at,
            )
            for event in events
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/health/live", tags=["health"])
def health_live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready", tags=["health"])
def health_ready(
    response: Response,
    db: Session = Depends(get_db),
    config: AppConfig = Depends(get_config),
) -> dict[str, Any]:
    database_ok = database_is_ready(db.get_bind())
    checks = {
        "database": database_ok,
        "ffprobe": ffprobe_available(config.ffprobe_path),
        "ffmpeg": ffmpeg_available(config.ffmpeg_path),
        "application_data": config.app_data_dir.is_dir(),
        "temporary_storage": config.temp_dir.is_dir(),
        "artwork_storage": config.artwork_dir.is_dir(),
    }
    ready = all(checks.values())
    if not ready:
        response.status_code = 503
    return {
        "status": "ready" if ready else "not_ready",
        "checks": {name: "ok" if available else "unavailable" for name, available in checks.items()},
    }


@router.get("/version", tags=["health"])
def version(product: ProductConfig = Depends(get_product_config)) -> dict[str, str]:
    return {"version": product.version}
