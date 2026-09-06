from __future__ import annotations

import asyncio
import contextlib
import logging
import traceback
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from time import monotonic
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.orm import sessionmaker

from app.api.catalog import router as catalog_router
from app.api.household import router as household_router
from app.api.media_roots import router as media_roots_router
from app.api.playback import router as playback_router
from app.api.remote_access import router as remote_access_router
from app.api.router import router
from app.config import get_config, get_product_config
from app.database import create_database_engine
from app.logging_config import configure_logging
from app.remote.local_auth import router as portal_local_router
from app.services.temp_cleanup import cleanup_stale_temp
from app.services.transcoding import PlaybackManager

config = get_config()
product = get_product_config()
configure_logging(config.log_level)
logger = logging.getLogger("api")


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    local_config = _app.dependency_overrides.get(get_config, get_config)()
    for directory in (local_config.app_data_dir, local_config.temp_dir, local_config.artwork_dir):
        directory.expanduser().mkdir(parents=True, exist_ok=True)
    removed = cleanup_stale_temp(local_config)
    if removed:
        logger.info("Stale application temporary entries removed", extra={"fields": {"count": removed}})
    local_engine = create_database_engine(local_config.database_url)
    manager = PlaybackManager(local_config, sessionmaker(bind=local_engine, expire_on_commit=False))
    _app.state.playback_manager = manager
    connector_task = None
    try:
        manager.start()
        if (
            local_config.deployment_mode == "native_windows"
            and local_config.native_data_dir
            and local_config.remote_control_dir
        ):
            from app.remote.connector import Connector
            from app.remote.media import RemoteMedia

            connector = Connector(
                local_config.remote_control_dir,
                local_config.native_data_dir / "remote-identity",
                product.version,
                media=RemoteMedia(local_config, sessionmaker(bind=local_engine, expire_on_commit=False), manager),
            )
            _app.state.portal_connector = connector
            _app.state.portal_pending = {}
            _app.state.portal_status_sessions = {}
            connector_task = asyncio.create_task(connector.run())
        yield
    finally:
        if connector_task:
            connector_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await connector_task
            _app.state.portal_pending.clear()
            _app.state.portal_status_sessions.clear()
        manager.close()
        local_engine.dispose()


app = FastAPI(
    title=product.name,
    description=product.subtitle,
    version=product.version,
    lifespan=lifespan,
    docs_url=None,
    openapi_url=f"{product.api_prefix}/openapi.json",
    redoc_url=None,
)
app.include_router(router, prefix=product.api_prefix)
app.include_router(catalog_router, prefix=product.api_prefix)
app.include_router(household_router, prefix=product.api_prefix)
app.include_router(media_roots_router, prefix=product.api_prefix)
app.include_router(playback_router, prefix=product.api_prefix)
app.include_router(remote_access_router, prefix=product.api_prefix)
app.include_router(portal_local_router)


@app.middleware("http")
async def request_context(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    started = monotonic()
    request_id = request.headers.get("X-Request-ID", "")
    try:
        uuid.UUID(request_id)
    except (ValueError, AttributeError):
        request_id = str(uuid.uuid4())
    request.state.request_id = request_id
    local_config = app.dependency_overrides.get(get_config, get_config)()
    if local_config.deployment_mode == "native_windows" and not (
        request.url.path.startswith("/portal/")
        or request.url.path
        in {f"{product.api_prefix}/health/live", f"{product.api_prefix}/health/ready", f"{product.api_prefix}/version"}
    ):
        # Native ordinary access is exclusively Portal-authenticated. The old
        # setup/password/media APIs are retained for existing container installs.
        return RedirectResponse("/portal/start", status_code=303)
    if (
        config.deployment_mode == "native_windows"
        and config.native_data_dir is not None
        and request.method == "POST"
        and request.url.path == f"{product.api_prefix}/playback/sessions"
        and (config.native_data_dir / "state" / "maintenance").exists()
    ):
        return _error_response(
            request, 503, "maintenance", "An installation update is in progress; retry after it finishes"
        )
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Cache-Control"] = "no-store"
    route = request.scope.get("route")
    logger.info(
        "HTTP request completed",
        extra={
            "fields": {
                "request_id": request_id,
                "method": request.method,
                "route": getattr(route, "path", "unmatched"),
                "status": response.status_code,
                "duration_ms": round((monotonic() - started) * 1000, 2),
            }
        },
    )
    return response


def _error_response(
    request: Request,
    status_code: int,
    code: str,
    message: str,
    fields: Any = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    error: dict[str, Any] = {
        "code": code,
        "message": message,
        "request_id": getattr(request.state, "request_id", None),
    }
    if fields is not None:
        error["fields"] = fields
    return JSONResponse(status_code=status_code, content={"error": error}, headers=headers)


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
    detail: Any = exc.detail
    if isinstance(detail, dict):
        message = str(detail.get("message", "Request could not be completed"))
        fields = {key: value for key, value in detail.items() if key != "message"}
    else:
        message = str(detail)
        fields = None
    return _error_response(
        request,
        exc.status_code,
        f"http_{exc.status_code}",
        message,
        fields,
        dict(exc.headers) if exc.headers else None,
    )


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    fields = [
        {
            "location": ".".join(str(item) for item in error.get("loc", ())),
            "message": error.get("msg", "Invalid value"),
        }
        for error in exc.errors()
    ]
    return _error_response(request, 422, "validation_error", "Request validation failed", fields)


@app.exception_handler(Exception)
async def unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    stack = [{"function": frame.name, "line": frame.lineno} for frame in traceback.extract_tb(exc.__traceback__)[-12:]]
    logger.error(
        "Unhandled request error",
        extra={
            "fields": {
                "request_id": getattr(request.state, "request_id", None),
                "exception_type": type(exc).__name__,
                "stack": stack,
            }
        },
    )
    return _error_response(request, 500, "internal_error", "The request could not be completed")
