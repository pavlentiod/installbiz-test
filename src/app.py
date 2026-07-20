"""FastAPI application assembly and process lifecycle."""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import Response

from .config import logger
from .database import engine, init_db
from .routes import resume_download_if_needed, router, stop_download_task


@asynccontextmanager
async def lifespan(_: FastAPI):
    await init_db()
    await resume_download_if_needed()
    try:
        yield
    finally:
        await stop_download_task()
        await engine.dispose()


app = FastAPI(
    title="Сервис скачивания и анализа файлов",
    description=(
        "Скачивает каталог через ограниченное внешнее API, сохраняет файлы в SQLite "
        "и рассчитывает частоту цифр."
    ),
    version="0.1.0",
    lifespan=lifespan,
)


@app.middleware("http")
async def log_requests(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    request_id = request.headers.get("X-Request-Id", uuid.uuid4().hex[:12])
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        elapsed_ms = (time.perf_counter() - started) * 1000
        logger.exception(
            "request_id=%s %s %s failed duration_ms=%.1f",
            request_id,
            request.method,
            request.url.path,
            elapsed_ms,
        )
        raise
    elapsed_ms = (time.perf_counter() - started) * 1000
    response.headers["X-Request-Id"] = request_id
    logger.info(
        "request_id=%s %s %s status=%s duration_ms=%.1f",
        request_id,
        request.method,
        request.url.path,
        response.status_code,
        elapsed_ms,
    )
    return response


app.include_router(router)
