"""SQLAlchemy models and persistence helpers."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy import Index, Integer, String, Text, func, select, text, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from .config import DATABASE_PATH, format_nsk, utc_now


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
        await session.execute(select(func.count(StoredFile.id), func.count(StoredFile.content)))
    ).one()
    await session.execute(
        update(DownloadState)
        .where(DownloadState.id == 1)
        .values(discovered_count=discovered_count, downloaded_count=downloaded_count)
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
