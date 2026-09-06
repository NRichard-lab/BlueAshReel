"""A runtime-scoped, exclusively bound random IPv4 loopback authorization listener."""
from __future__ import annotations

import asyncio
import socket
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager

import uvicorn
from fastapi import FastAPI, Request, Response


class CallbackServer(uvicorn.Server):
    # The outer native API owns process signals and runtime shutdown.
    @contextmanager
    def capture_signals(self) -> Iterator[None]:
        yield


class CallbackListener:
    def __init__(self, application: FastAPI) -> None:
        from app.remote.local_auth import router

        callback_app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
        callback_app.state = application.state
        callback_app.include_router(router)

        @callback_app.middleware("http")
        async def private_response(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
            response = await call_next(request)
            response.headers["Cache-Control"] = "no-store"
            response.headers["Referrer-Policy"] = "no-referrer"
            response.headers["X-Content-Type-Options"] = "nosniff"
            return response

        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        self.socket.bind(("127.0.0.1", 0))
        self.socket.setblocking(False)
        self.origin = f"http://127.0.0.1:{self.socket.getsockname()[1]}"
        self.server = CallbackServer(uvicorn.Config(
            callback_app, host="127.0.0.1", port=0, lifespan="off", log_config=None, access_log=False,
            timeout_keep_alive=2, timeout_graceful_shutdown=2, limit_concurrency=16,
            h11_max_incomplete_event_size=8192,
        ))
        self.task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self.task = asyncio.create_task(self.server.serve(sockets=[self.socket]))
        for _ in range(100):
            if self.server.started:
                return
            if self.task.done():
                await self.task
                break
            await asyncio.sleep(0.01)
        await self.close()
        raise RuntimeError("The secure loopback callback listener could not start")

    async def close(self) -> None:
        self.server.should_exit = True
        if self.task is not None:
            await self.task
        self.socket.close()
