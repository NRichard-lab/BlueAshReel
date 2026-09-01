from __future__ import annotations

import logging
import traceback
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from time import monotonic
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api.catalog import router as catalog_router
from app.api.household import router as household_router
from app.api.playback import router as playback_router
from app.api.router import router
from app.config import get_config, get_product_config
from app.logging_config import configure_logging
from app.services.temp_cleanup import cleanup_stale_temp

config = get_config()
product = get_product_config()
configure_logging(config.log_level)
logger = logging.getLogger("api")


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    for directory in (config.app_data_dir, config.temp_dir, config.artwork_dir):
        directory.expanduser().mkdir(parents=True, exist_ok=True)
    removed = cleanup_stale_temp(config)
    if removed:
        logger.info("Stale application temporary entries removed", extra={"fields": {"count": removed}})
    yield


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
app.include_router(playback_router, prefix=product.api_prefix)


@app.middleware("http")
async def request_context(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    started = monotonic()
    request_id = request.headers.get("X-Request-ID", "")
    try:
        uuid.UUID(request_id)
    except (ValueError, AttributeError):
        request_id = str(uuid.uuid4())
    request.state.request_id = request_id
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
