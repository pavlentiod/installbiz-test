"""Upstream API access, retry policy, ZIP validation, and download workflow."""

from __future__ import annotations

import asyncio
import io
import math
import random
import re
import time
import zipfile
from collections.abc import Awaitable, Callable, Iterator, Sequence
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

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

from .config import (
    BATCH_SIZE,
    CANDIDATE_ID,
    DEFAULT_RETRY_SECONDS,
    MAX_ARCHIVE_BYTES,
    MAX_FILE_BYTES,
    MAX_TRANSIENT_RETRIES,
    REQUEST_INTERVAL_SECONDS,
    REQUEST_TIMEOUT_SECONDS,
    UPSTREAM_BASE_URL,
    logger,
    utc_now,
)
from .database import (
    acknowledge_files,
    get_pending_names,
    refresh_download_counts,
    remember_names,
    save_files,
    update_download_state,
)


class UpstreamError(RuntimeError):
    pass


request_interval_lock = asyncio.Lock()
last_request_started_at: float | None = None


async def wait_for_request_interval() -> None:
    """Serialize upstream calls and keep their start times sufficiently far apart."""
    global last_request_started_at
    async with request_interval_lock:
        if last_request_started_at is not None:
            elapsed = time.monotonic() - last_request_started_at
            delay = REQUEST_INTERVAL_SECONDS - elapsed
            if delay > 0:
                logger.debug("Waiting %.2f seconds before the next upstream request", delay)
                await asyncio.sleep(delay)
        last_request_started_at = time.monotonic()


def chunks(items: Sequence[str], size: int = BATCH_SIZE) -> Iterator[list[str]]:
    """Yield bounded batches without allocating a second list for all items."""
    if size < 1:
        raise ValueError("Chunk size must be greater than zero")
    for index in range(0, len(items), size):
        yield list(items[index : index + size])


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
            await wait_for_request_interval()
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
    if not 1 <= len(unique_names) <= BATCH_SIZE:
        raise ValueError(f"A download batch must contain from one to {BATCH_SIZE} unique names")
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
