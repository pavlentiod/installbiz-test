"""HTTP routes, page rendering, calculations, and background-task coordination."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections import Counter
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError

from .config import DIGITS, PROJECT_ROOT, format_nsk, logger, utc_now
from .database import (
    Calculation,
    Session,
    StoredFile,
    engine,
    get_download_state,
    refresh_download_counts,
    update_download_state,
)
from .downloader import run_download

router = APIRouter()
templates = Jinja2Templates(directory=str(PROJECT_ROOT / "templates"))
templates.env.filters["nsk"] = format_nsk


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


async def resume_download_if_needed() -> None:
    current = await get_download_state()
    if current["status"] in {"queued", "running", "waiting_retry"}:
        logger.info("Resuming interrupted download")
        await start_download_task(new_run=False)


async def stop_download_task() -> None:
    global download_task
    if download_task is not None and not download_task.done():
        download_task.cancel()
        await asyncio.gather(download_task, return_exceptions=True)
    download_task = None


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
async def home_page(request: Request) -> Response:
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"state": await get_download_state()},
    )


@router.post(
    "/api/download/start",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Запустить скачивание каталога",
    description="Запускает один фоновый процесс или возвращает уже активный процесс.",
)
async def start_download() -> dict[str, Any]:
    return await start_download_task(new_run=True)


@router.get(
    "/api/download/status",
    summary="Получить прогресс скачивания",
    description="Возвращает сохранённый статус и счётчики текущего процесса.",
)
async def download_status() -> dict[str, Any]:
    return await get_download_state()


@router.get("/files", response_class=HTMLResponse, include_in_schema=False)
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


@router.post(
    "/api/calculations",
    status_code=status.HTTP_201_CREATED,
    summary="Рассчитать статистику цифр",
    description="Считает общую и пофайловую статистику для выбранных либо всех файлов.",
)
async def create_calculation(selection: CalculationRequest) -> dict[str, str]:
    ids: list[int] = []
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
            assert content is not None
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


@router.get(
    "/calculations/{calculation_id}",
    response_class=HTMLResponse,
    include_in_schema=False,
)
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


@router.get(
    "/health/live",
    summary="Проверка процесса",
    description="Подтверждает, что HTTP-процесс приложения работает.",
)
async def liveness() -> dict[str, str]:
    return {"status": "ok"}


@router.get(
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
