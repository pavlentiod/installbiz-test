"""Lightweight FastAPI application for downloading and analysing a file catalog.

The application deliberately keeps its business logic in this module: the test task is
small, and splitting it into repository/service layers would make it harder to review.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import logging.config
import math
import os
import random
import re
import time
import uuid
import zipfile
from collections import Counter
from collections.abc import Awaitable, Callable, Iterator, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

import httpx
from fastapi import FastAPI, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from sqlalchemy import Index, Integer, String, Text, func, select, text, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from developer_api_client import Client
from developer_api_client.api.файлы import (
    download_files_api_files_download_post as download_api,
)
from developer_api_client.api.файлы import (
    get_random_file_names_api_files_names_get as names_api,
)
from developer_api_client.api.файлы import (
    mark_files_downloaded_api_files_downloaded_post as downloaded_api,
)
from developer_api_client.models.download_request import DownloadRequest
from developer_api_client.models.mark_downloaded_request import MarkDownloadedRequest

# Configuration and logging

BASE_DIR = Path(__file__).resolve().parent
DATABASE_PATH = Path(os.getenv("DATABASE_PATH", str(BASE_DIR / "data" / "app.db")))
UPSTREAM_BASE_URL = os.getenv("UPSTREAM_BASE_URL", "http://127.0.0.1:8001").rstrip("/")
CANDIDATE_ID = os.getenv("CANDIDATE_ID", "local-candidate").strip()
MAX_ARCHIVE_BYTES = 1_000_000
MAX_FILE_BYTES = 2_000
BATCH_SIZE = 3
DIGITS = "0123456789"
NSK = ZoneInfo("Asia/Novosibirsk")

logging.config.dictConfig(
    {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "console": {
                "format": "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
                "datefmt": "%Y-%m-%d %H:%M:%S",
            }
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "formatter": "console",
            }
        },
        "root": {
            "handlers": ["console"],
            "level": os.getenv("LOG_LEVEL", "INFO").upper(),
        },
    }
)
logger = logging.getLogger("file_catalog")


def positive_int_env(name: str, default: int) -> int:
    """Read a strictly positive integer setting or fail with a useful message."""
    raw_value = os.getenv(name, str(default))
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer, got {raw_value!r}") from exc
    if value < 1:
        raise RuntimeError(f"{name} must be greater than zero, got {value}")
    return value


def positive_float_env(name: str, default: float) -> float:
    """Read a finite positive floating-point setting."""
    raw_value = os.getenv(name, str(default))
    try:
        value = float(raw_value)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a number, got {raw_value!r}") from exc
    if not math.isfinite(value) or value <= 0:
        raise RuntimeError(f"{name} must be a finite number greater than zero, got {raw_value!r}")
    return value


REQUEST_TIMEOUT_SECONDS = positive_float_env("REQUEST_TIMEOUT_SECONDS", 30)
DEFAULT_RETRY_SECONDS = positive_int_env("DEFAULT_RETRY_SECONDS", 10)
MAX_TRANSIENT_RETRIES = positive_int_env("MAX_TRANSIENT_RETRIES", 5)

if not CANDIDATE_ID:
    raise RuntimeError("CANDIDATE_ID must not be empty")
if not UPSTREAM_BASE_URL:
    raise RuntimeError("UPSTREAM_BASE_URL must not be empty")


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def format_nsk(value: str | None) -> str:
    if not value:
        return "—"
    try:
        timestamp = datetime.fromisoformat(value)
    except ValueError:
        logger.warning("Invalid timestamp in the database: %r", value)
        return "—"
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    return timestamp.astimezone(NSK).strftime("%d.%m.%Y %H:%M:%S НСК")


templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
templates.env.filters["nsk"] = format_nsk


# SQLAlchemy models and database helpers. No migrations are needed for this test task:
# create_all() initializes an empty SQLite file on application startup.


class Base(DeclarativeBase):
    pass


class DownloadState(Base):
    __tablename__ = "download_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="idle")
    started_at: Mapped[str | None] = mapped_column(String(64))
    finished_at: Mapped[str | None] = mapped_column(String(64))
    next_retry_at: Mapped[str | None] = mapped_column(String(64))
    discovered_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    downloaded_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text)


class StoredFile(Base):
    __tablename__ = "files"
    __table_args__ = (Index("idx_files_downloaded_at", "downloaded_at", "id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    content: Mapped[str | None] = mapped_column(Text)
    downloaded_at: Mapped[str | None] = mapped_column(String(64))
    acknowledged: Mapped[bool] = mapped_column(nullable=False, default=False)


class Calculation(Base):
    __tablename__ = "calculations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    created_at: Mapped[str] = mapped_column(String(64), nullable=False)
    file_count: Mapped[int] = mapped_column(Integer, nullable=False)
    total_counts_json: Mapped[str] = mapped_column(Text, nullable=False)
    file_counts_json: Mapped[str] = mapped_column(Text, nullable=False)


database_url = f"sqlite+aiosqlite:///{DATABASE_PATH.as_posix()}"
engine = create_async_engine(database_url, connect_args={"timeout": 5})
Session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def init_db() -> None:
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    async with engine.begin() as connection:
        await connection.execute(text("PRAGMA journal_mode=WAL"))
        await connection.execute(text("PRAGMA foreign_keys=ON"))
        await connection.execute(text("PRAGMA busy_timeout=5000"))
        await connection.run_sync(Base.metadata.create_all)
        await connection.execute(
            sqlite_insert(DownloadState)
            .values(id=1, status="idle", discovered_count=0, downloaded_count=0)
            .on_conflict_do_nothing(index_elements=[DownloadState.id])
        )


async def get_download_state() -> dict[str, Any]:
    async with Session() as session:
        state = await session.get(DownloadState, 1)
        if state is None:
            raise RuntimeError("Download state is not initialized")
        result = {
            "id": state.id,
            "status": state.status,
            "started_at": state.started_at,
            "finished_at": state.finished_at,
            "next_retry_at": state.next_retry_at,
            "discovered_count": state.discovered_count,
            "downloaded_count": state.downloaded_count,
            "error": state.error,
        }
        result["started_at_nsk"] = format_nsk(state.started_at)
        result["next_retry_at_nsk"] = format_nsk(state.next_retry_at)
        return result


async def update_download_state(**values: Any) -> None:
    allowed = {
        "status",
        "started_at",
        "finished_at",
        "next_retry_at",
        "discovered_count",
        "downloaded_count",
        "error",
    }
    if not values or not set(values).issubset(allowed):
        raise ValueError("Unexpected download state fields")
    async with Session.begin() as session:
        await session.execute(update(DownloadState).where(DownloadState.id == 1).values(**values))


async def _refresh_download_counts(session: AsyncSession) -> None:
    discovered_count, downloaded_count = (
        await session.execute(
            select(
                func.count(StoredFile.id),
                func.count(StoredFile.content),
            )
        )
    ).one()
    await session.execute(
        update(DownloadState)
        .where(DownloadState.id == 1)
        .values(
            discovered_count=discovered_count,
            downloaded_count=downloaded_count,
        )
    )


async def refresh_download_counts() -> None:
    async with Session.begin() as session:
        await _refresh_download_counts(session)


async def remember_names(names: Sequence[str]) -> None:
    unique_names = list(dict.fromkeys(names))
    if not unique_names:
        return
    async with Session.begin() as session:
        await session.execute(
            sqlite_insert(StoredFile)
            .values([{"name": name, "acknowledged": False} for name in unique_names])
            .on_conflict_do_nothing(index_elements=[StoredFile.name])
        )
        await _refresh_download_counts(session)


async def get_pending_names(*, downloaded: bool) -> list[str]:
    condition = (
        StoredFile.content.is_not(None) & StoredFile.acknowledged.is_(False)
        if downloaded
        else StoredFile.content.is_(None)
    )
    async with Session() as session:
        result = await session.scalars(
            select(StoredFile.name).where(condition).order_by(StoredFile.id)
        )
        return list(result)


async def save_files(files: dict[str, str]) -> None:
    if not files:
        return
    downloaded_at = utc_now()
    async with Session.begin() as session:
        statement = sqlite_insert(StoredFile).values(
            [
                {
                    "name": name,
                    "content": content,
                    "downloaded_at": downloaded_at,
                    "acknowledged": False,
                }
                for name, content in files.items()
            ]
        )
        await session.execute(
            statement.on_conflict_do_update(
                index_elements=[StoredFile.name],
                set_={
                    "content": func.coalesce(StoredFile.content, statement.excluded.content),
                    "downloaded_at": func.coalesce(
                        StoredFile.downloaded_at, statement.excluded.downloaded_at
                    ),
                },
            )
        )
        await _refresh_download_counts(session)


async def acknowledge_files(names: Sequence[str]) -> None:
    if not names:
        return
    async with Session.begin() as session:
        await session.execute(
            update(StoredFile)
            .where(StoredFile.name.in_(names), StoredFile.content.is_not(None))
            .values(acknowledged=True)
        )


def chunks(items: Sequence[str], size: int = BATCH_SIZE) -> Iterator[list[str]]:
    """Yield bounded batches without allocating a second list for all items."""
    if size < 1:
        raise ValueError("Chunk size must be greater than zero")
    for index in range(0, len(items), size):
        yield list(items[index : index + size])


# Upstream API, Retry-After handling, ZIP validation, and download workflow


class UpstreamError(RuntimeError):
    pass


def retry_after_seconds(value: str | None) -> int:
    if not value:
        return DEFAULT_RETRY_SECONDS
    try:
        return max(1, int(value))
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(value)
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=UTC)
            return max(1, math.ceil((retry_at - datetime.now(UTC)).total_seconds()))
        except (TypeError, ValueError, OverflowError):
            logger.warning("Invalid Retry-After value %r; using fallback", value)
            return DEFAULT_RETRY_SECONDS


def response_error(response: Any) -> str:
    detail = getattr(getattr(response, "parsed", None), "detail", None)
    if detail:
        return str(detail)
    content = getattr(response, "content", b"")
    text = content.decode("utf-8", errors="replace").strip()
    return text[:500] or "empty response"


async def request_with_retry(
    operation: str,
    request_factory: Callable[[], Awaitable[Any]],
) -> Any:
    transient_attempt = 0
    while True:
        try:
            response = await request_factory()
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            transient_attempt += 1
            if transient_attempt > MAX_TRANSIENT_RETRIES:
                raise UpstreamError(f"{operation}: network retries exhausted: {exc}") from exc
            delay = min(60, 2 ** (transient_attempt - 1)) + random.random()
            logger.warning("%s failed (%s); retry in %.1f seconds", operation, exc, delay)
            await asyncio.sleep(delay)
            continue

        code = int(response.status_code)
        if code == 200:
            return response

        if code in {403, 429}:
            delay = retry_after_seconds(response.headers.get("Retry-After"))
            retry_at = datetime.fromtimestamp(time.time() + delay, tz=UTC).isoformat()
            await update_download_state(
                status="waiting_retry",
                next_retry_at=retry_at,
                error=f"{operation}: HTTP {code}: {response_error(response)}",
            )
            logger.warning("%s returned HTTP %s; retry in %s seconds", operation, code, delay)
            await asyncio.sleep(delay)
            await update_download_state(status="running", next_retry_at=None, error=None)
            continue

        if code >= 500:
            transient_attempt += 1
            if transient_attempt > MAX_TRANSIENT_RETRIES:
                raise UpstreamError(f"{operation}: HTTP {code}, retries exhausted")
            delay = min(60, 2 ** (transient_attempt - 1)) + random.random()
            logger.warning("%s returned HTTP %s; retry in %.1f seconds", operation, code, delay)
            await asyncio.sleep(delay)
            continue

        raise UpstreamError(f"{operation}: HTTP {code}: {response_error(response)}")


def validate_archive(payload: bytes, requested_names: Sequence[str]) -> dict[str, str]:
    if not payload or len(payload) > MAX_ARCHIVE_BYTES:
        raise UpstreamError("ZIP archive is empty or exceeds the size limit")

    expected = list(dict.fromkeys(requested_names))
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            entries = archive.infolist()
            entry_names = [entry.filename for entry in entries]
            if len(entry_names) != len(set(entry_names)):
                raise UpstreamError("ZIP contains duplicate file names")
            if len(entries) != len(expected) or set(entry_names) != set(expected):
                raise UpstreamError("ZIP contents do not exactly match requested file names")

            result: dict[str, str] = {}
            for entry in entries:
                if entry.is_dir() or (entry.flag_bits & 0x1) or entry.file_size > MAX_FILE_BYTES:
                    raise UpstreamError(f"Unsafe ZIP entry: {entry.filename}")
                raw = archive.read(entry)
                if len(raw) > MAX_FILE_BYTES:
                    raise UpstreamError(f"File is too large: {entry.filename}")
                try:
                    text = raw.decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise UpstreamError(f"File is not UTF-8: {entry.filename}") from exc
                if text.endswith("\r\n"):
                    text = text[:-2]
                elif text.endswith("\n"):
                    text = text[:-1]
                if re.fullmatch(r"[0-9]{500}", text) is None:
                    raise UpstreamError(f"File {entry.filename} must contain exactly 500 digits")
                result[entry.filename] = text
            return result
    except zipfile.BadZipFile as exc:
        raise UpstreamError("Upstream response is not a valid ZIP archive") from exc


async def mark_downloaded(client: Client, names: Sequence[str]) -> None:
    unique_names = list(dict.fromkeys(names))
    response = await request_with_retry(
        "POST /api/files/downloaded",
        lambda: downloaded_api.asyncio_detailed(
            client=client,
            body=MarkDownloadedRequest(file_names=unique_names),
        ),
    )
    parsed = response.parsed
    if parsed is None or parsed.marked_now + parsed.already_marked != len(unique_names):
        raise UpstreamError("Upstream returned inconsistent acknowledgement counters")
    await acknowledge_files(unique_names)
    logger.info(
        "Acknowledged %s files (new=%s, already=%s)",
        len(unique_names),
        parsed.marked_now,
        parsed.already_marked,
    )


async def download_batch(client: Client, names: Sequence[str]) -> None:
    unique_names = list(dict.fromkeys(names))
    if not 1 <= len(unique_names) <= 3:
        raise ValueError("A download batch must contain from one to three unique names")
    response = await request_with_retry(
        "POST /api/files/download",
        lambda: download_api.asyncio_detailed(
            client=client,
            body=DownloadRequest(file_names=unique_names),
        ),
    )
    content_type = response.headers.get("Content-Type", "").lower()
    if not content_type.startswith("application/zip"):
        raise UpstreamError(f"Unexpected download Content-Type: {content_type or 'missing'}")
    files = validate_archive(response.content, unique_names)
    await save_files(files)
    await mark_downloaded(client, unique_names)
    logger.info("Downloaded and saved %s files", len(unique_names))


async def run_download() -> None:
    await update_download_state(
        status="running",
        finished_at=None,
        next_retry_at=None,
        error=None,
    )
    client = Client(
        base_url=UPSTREAM_BASE_URL,
        headers={"X-Candidate-Id": CANDIDATE_ID},
        timeout=httpx.Timeout(REQUEST_TIMEOUT_SECONDS),
        follow_redirects=False,
    )
    try:
        async with client:
            while True:
                unacknowledged = await get_pending_names(downloaded=True)
                for batch in chunks(unacknowledged):
                    await mark_downloaded(client, batch)

                pending = await get_pending_names(downloaded=False)
                for batch in chunks(pending):
                    await download_batch(client, batch)

                response = await request_with_retry(
                    "GET /api/files/names",
                    lambda: names_api.asyncio_detailed(client=client),
                )
                parsed = response.parsed
                if parsed is None:
                    raise UpstreamError("Upstream returned an invalid names response")
                names = list(dict.fromkeys(parsed.file_names))
                if not names:
                    await refresh_download_counts()
                    await update_download_state(
                        status="completed",
                        finished_at=utc_now(),
                        next_retry_at=None,
                        error=None,
                    )
                    logger.info("Catalog download completed")
                    return

                await remember_names(names)
                logger.info("Discovered %s names in the current response", len(names))
                for batch in chunks(names):
                    await download_batch(client, batch)
    except asyncio.CancelledError:
        logger.info("Download task was stopped")
        raise
    except Exception as exc:
        logger.exception("Catalog download failed")
        await update_download_state(
            status="failed",
            finished_at=utc_now(),
            next_retry_at=None,
            error=str(exc)[:1000],
        )


# Calculations and HTTP routes


class CalculationRequest(BaseModel):
    """Selection used to start a digit-frequency calculation."""

    mode: Literal["selected", "all"]
    file_ids: list[int] = Field(default_factory=list, max_length=10_000)


download_task: asyncio.Task[None] | None = None
download_task_lock = asyncio.Lock()


async def start_download_task(*, new_run: bool) -> dict[str, Any]:
    global download_task
    async with download_task_lock:
        if download_task is not None and not download_task.done():
            return await get_download_state()
        if new_run:
            await refresh_download_counts()
            await update_download_state(
                status="queued",
                started_at=utc_now(),
                finished_at=None,
                next_retry_at=None,
                error=None,
            )
        download_task = asyncio.create_task(run_download(), name="catalog-download")
        return await get_download_state()


@asynccontextmanager
async def lifespan(_: FastAPI):
    global download_task
    await init_db()
    current = await get_download_state()
    if current["status"] in {"queued", "running", "waiting_retry"}:
        logger.info("Resuming interrupted download")
        await start_download_task(new_run=False)
    try:
        yield
    finally:
        if download_task is not None and not download_task.done():
            download_task.cancel()
            await asyncio.gather(download_task, return_exceptions=True)
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


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def home_page(request: Request) -> Response:
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"state": await get_download_state()},
    )


@app.post(
    "/api/download/start",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Запустить скачивание каталога",
    description="Запускает один фоновый процесс или возвращает уже активный процесс.",
)
async def start_download() -> dict[str, Any]:
    return await start_download_task(new_run=True)


@app.get(
    "/api/download/status",
    summary="Получить прогресс скачивания",
    description="Возвращает сохранённый статус и счётчики текущего процесса.",
)
async def download_status() -> dict[str, Any]:
    return await get_download_state()


@app.get("/files", response_class=HTMLResponse, include_in_schema=False)
async def files_page(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=5, le=100),
    order: Literal["desc", "asc"] = "desc",
) -> Response:
    ordering = (
        (StoredFile.downloaded_at.desc(), StoredFile.id.desc())
        if order == "desc"
        else (StoredFile.downloaded_at.asc(), StoredFile.id.asc())
    )
    async with Session() as session:
        total = await session.scalar(
            select(func.count(StoredFile.id)).where(StoredFile.content.is_not(None))
        )
        total = total or 0
        pages = max(1, (total + page_size - 1) // page_size)
        page = min(page, pages)
        rows = await session.execute(
            select(StoredFile.id, StoredFile.name, StoredFile.downloaded_at)
            .where(StoredFile.content.is_not(None))
            .order_by(*ordering)
            .limit(page_size)
            .offset((page - 1) * page_size)
        )
        files = [dict(row._mapping) for row in rows]
    return templates.TemplateResponse(
        request=request,
        name="files.html",
        context={
            "files": files,
            "page": page,
            "pages": pages,
            "page_size": page_size,
            "order": order,
            "total": total,
        },
    )


@app.post(
    "/api/calculations",
    status_code=status.HTTP_201_CREATED,
    summary="Рассчитать статистику цифр",
    description="Считает общую и пофайловую статистику для выбранных либо всех файлов.",
)
async def create_calculation(selection: CalculationRequest) -> dict[str, str]:
    async with Session.begin() as session:
        statement = select(
            StoredFile.id,
            StoredFile.name,
            StoredFile.content,
            StoredFile.downloaded_at,
        ).where(StoredFile.content.is_not(None))
        if selection.mode == "all":
            statement = statement.order_by(StoredFile.id)
        else:
            ids = list(dict.fromkeys(selection.file_ids))
            if not ids:
                raise HTTPException(status_code=400, detail="Не выбрано ни одного файла")
            statement = statement.where(StoredFile.id.in_(ids)).order_by(StoredFile.id)
        rows = (await session.execute(statement)).all()
        if not rows:
            raise HTTPException(status_code=400, detail="Нет файлов для расчёта")
        if selection.mode == "selected":
            found_ids = {row.id for row in rows}
            missing_ids = [file_id for file_id in ids if file_id not in found_ids]
            if missing_ids:
                shown_ids = ", ".join(map(str, missing_ids[:10]))
                suffix = "…" if len(missing_ids) > 10 else ""
                raise HTTPException(
                    status_code=400,
                    detail=f"Файлы не найдены: {shown_ids}{suffix}",
                )

        total_counter: Counter[str] = Counter()
        file_results: list[dict[str, Any]] = []
        for row in rows:
            content = row.content
            valid_content = (
                content is not None
                and len(content) == 500
                and content.isascii()
                and content.isdecimal()
            )
            if not valid_content:
                raise HTTPException(status_code=500, detail=f"Некорректный файл: {row.name}")
            assert content is not None  # Narrow the ORM nullable type after validation.
            counter = Counter(content)
            counts = {digit: counter.get(digit, 0) for digit in DIGITS}
            total_counter.update(counter)
            file_results.append(
                {
                    "id": row.id,
                    "name": row.name,
                    "downloaded_at": row.downloaded_at,
                    "counts": counts,
                }
            )

        total_counts = {digit: total_counter.get(digit, 0) for digit in DIGITS}
        calculation_id = str(uuid.uuid4())
        session.add(
            Calculation(
                id=calculation_id,
                created_at=utc_now(),
                file_count=len(rows),
                total_counts_json=json.dumps(total_counts, separators=(",", ":")),
                file_counts_json=json.dumps(
                    file_results,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            )
        )
    return {"id": calculation_id, "url": f"/calculations/{calculation_id}"}


@app.get("/calculations/{calculation_id}", response_class=HTMLResponse, include_in_schema=False)
async def calculation_page(request: Request, calculation_id: str) -> Response:
    async with Session() as session:
        row = await session.get(Calculation, calculation_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Расчёт не найден")
    try:
        calculation = {
            "id": row.id,
            "created_at": row.created_at,
            "file_count": row.file_count,
            "total_counts": json.loads(row.total_counts_json),
            "file_counts": json.loads(row.file_counts_json),
        }
    except json.JSONDecodeError as exc:
        logger.exception("Calculation %s contains invalid JSON", calculation_id)
        raise HTTPException(status_code=500, detail="Данные расчёта повреждены") from exc
    return templates.TemplateResponse(
        request=request,
        name="calculation.html",
        context={"calculation": calculation},
    )


@app.get(
    "/health/live",
    summary="Проверка процесса",
    description="Подтверждает, что HTTP-процесс приложения работает.",
)
async def liveness() -> dict[str, str]:
    return {"status": "ok"}


@app.get(
    "/health/ready",
    summary="Проверка готовности",
    description="Проверяет доступность SQLite перед приёмом трафика.",
)
async def readiness() -> dict[str, str]:
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="SQLite is unavailable") from exc
    return {"status": "ok"}
