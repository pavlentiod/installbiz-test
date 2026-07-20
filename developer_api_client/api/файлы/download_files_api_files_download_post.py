from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.download_request import DownloadRequest
from ...models.error_response import ErrorResponse
from ...models.http_validation_error import HTTPValidationError
from ...types import Response


def _get_kwargs(
    *,
    body: DownloadRequest,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/api/files/download",
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | HTTPValidationError | None:
    if response.status_code == 403:
        response_403 = ErrorResponse.from_dict(response.json())

        return response_403

    if response.status_code == 404:
        response_404 = ErrorResponse.from_dict(response.json())

        return response_404

    if response.status_code == 422:
        response_422 = HTTPValidationError.from_dict(response.json())

        return response_422

    if response.status_code == 429:
        response_429 = ErrorResponse.from_dict(response.json())

        return response_429

    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[ErrorResponse | HTTPValidationError]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
    body: DownloadRequest,
) -> Response[ErrorResponse | HTTPValidationError]:
    """Скачать файлы по именам

     Отдать запрошенные файлы одним ZIP-архивом.

    За один запрос можно получить не более 3 файлов — скачивание
    «всего и сразу» намеренно невозможно. Скачивание не отмечает файлы
    как полученные: об этом нужно отдельно сообщить ручкой
    ``POST /api/files/downloaded``.

    Args:
        body (DownloadRequest): Запрос на скачивание файлов по именам.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        body=body,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: AuthenticatedClient | Client,
    body: DownloadRequest,
) -> ErrorResponse | HTTPValidationError | None:
    """Скачать файлы по именам

     Отдать запрошенные файлы одним ZIP-архивом.

    За один запрос можно получить не более 3 файлов — скачивание
    «всего и сразу» намеренно невозможно. Скачивание не отмечает файлы
    как полученные: об этом нужно отдельно сообщить ручкой
    ``POST /api/files/downloaded``.

    Args:
        body (DownloadRequest): Запрос на скачивание файлов по именам.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | HTTPValidationError
    """

    return sync_detailed(
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    body: DownloadRequest,
) -> Response[ErrorResponse | HTTPValidationError]:
    """Скачать файлы по именам

     Отдать запрошенные файлы одним ZIP-архивом.

    За один запрос можно получить не более 3 файлов — скачивание
    «всего и сразу» намеренно невозможно. Скачивание не отмечает файлы
    как полученные: об этом нужно отдельно сообщить ручкой
    ``POST /api/files/downloaded``.

    Args:
        body (DownloadRequest): Запрос на скачивание файлов по именам.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
    body: DownloadRequest,
) -> ErrorResponse | HTTPValidationError | None:
    """Скачать файлы по именам

     Отдать запрошенные файлы одним ZIP-архивом.

    За один запрос можно получить не более 3 файлов — скачивание
    «всего и сразу» намеренно невозможно. Скачивание не отмечает файлы
    как полученные: об этом нужно отдельно сообщить ручкой
    ``POST /api/files/downloaded``.

    Args:
        body (DownloadRequest): Запрос на скачивание файлов по именам.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | HTTPValidationError
    """

    return (
        await asyncio_detailed(
            client=client,
            body=body,
        )
    ).parsed
