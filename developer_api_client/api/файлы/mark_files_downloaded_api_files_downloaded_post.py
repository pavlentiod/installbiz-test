from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.http_validation_error import HTTPValidationError
from ...models.mark_downloaded_request import MarkDownloadedRequest
from ...models.mark_downloaded_response import MarkDownloadedResponse
from ...types import UNSET, Response, Unset


def _get_kwargs(
    *,
    body: MarkDownloadedRequest,
    x_candidate_id: None | str | Unset = UNSET,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}
    if not isinstance(x_candidate_id, Unset):
        headers["x-candidate-id"] = x_candidate_id

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/api/files/downloaded",
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | HTTPValidationError | MarkDownloadedResponse | None:
    if response.status_code == 200:
        response_200 = MarkDownloadedResponse.from_dict(response.json())

        return response_200

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
) -> Response[ErrorResponse | HTTPValidationError | MarkDownloadedResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
    body: MarkDownloadedRequest,
    x_candidate_id: None | str | Unset = UNSET,
) -> Response[ErrorResponse | HTTPValidationError | MarkDownloadedResponse]:
    """Отметить файлы как скачанные

     Зафиксировать, что кандидат скачал перечисленные файлы.

    Отмеченные файлы больше не попадают в выдачу ручки
    ``GET /api/files/names`` для этого кандидата. Повторная отметка
    не является ошибкой и учитывается в поле ``already_marked``.

    Args:
        x_candidate_id (None | str | Unset): Необязательный собственный идентификатор кандидата.
            Если не передан, кандидат идентифицируется по IP-адресу.
        body (MarkDownloadedRequest): Запрос на отметку файлов как скачанных кандидатом.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | HTTPValidationError | MarkDownloadedResponse]
    """

    kwargs = _get_kwargs(
        body=body,
        x_candidate_id=x_candidate_id,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: AuthenticatedClient | Client,
    body: MarkDownloadedRequest,
    x_candidate_id: None | str | Unset = UNSET,
) -> ErrorResponse | HTTPValidationError | MarkDownloadedResponse | None:
    """Отметить файлы как скачанные

     Зафиксировать, что кандидат скачал перечисленные файлы.

    Отмеченные файлы больше не попадают в выдачу ручки
    ``GET /api/files/names`` для этого кандидата. Повторная отметка
    не является ошибкой и учитывается в поле ``already_marked``.

    Args:
        x_candidate_id (None | str | Unset): Необязательный собственный идентификатор кандидата.
            Если не передан, кандидат идентифицируется по IP-адресу.
        body (MarkDownloadedRequest): Запрос на отметку файлов как скачанных кандидатом.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | HTTPValidationError | MarkDownloadedResponse
    """

    return sync_detailed(
        client=client,
        body=body,
        x_candidate_id=x_candidate_id,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    body: MarkDownloadedRequest,
    x_candidate_id: None | str | Unset = UNSET,
) -> Response[ErrorResponse | HTTPValidationError | MarkDownloadedResponse]:
    """Отметить файлы как скачанные

     Зафиксировать, что кандидат скачал перечисленные файлы.

    Отмеченные файлы больше не попадают в выдачу ручки
    ``GET /api/files/names`` для этого кандидата. Повторная отметка
    не является ошибкой и учитывается в поле ``already_marked``.

    Args:
        x_candidate_id (None | str | Unset): Необязательный собственный идентификатор кандидата.
            Если не передан, кандидат идентифицируется по IP-адресу.
        body (MarkDownloadedRequest): Запрос на отметку файлов как скачанных кандидатом.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | HTTPValidationError | MarkDownloadedResponse]
    """

    kwargs = _get_kwargs(
        body=body,
        x_candidate_id=x_candidate_id,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
    body: MarkDownloadedRequest,
    x_candidate_id: None | str | Unset = UNSET,
) -> ErrorResponse | HTTPValidationError | MarkDownloadedResponse | None:
    """Отметить файлы как скачанные

     Зафиксировать, что кандидат скачал перечисленные файлы.

    Отмеченные файлы больше не попадают в выдачу ручки
    ``GET /api/files/names`` для этого кандидата. Повторная отметка
    не является ошибкой и учитывается в поле ``already_marked``.

    Args:
        x_candidate_id (None | str | Unset): Необязательный собственный идентификатор кандидата.
            Если не передан, кандидат идентифицируется по IP-адресу.
        body (MarkDownloadedRequest): Запрос на отметку файлов как скачанных кандидатом.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | HTTPValidationError | MarkDownloadedResponse
    """

    return (
        await asyncio_detailed(
            client=client,
            body=body,
            x_candidate_id=x_candidate_id,
        )
    ).parsed
