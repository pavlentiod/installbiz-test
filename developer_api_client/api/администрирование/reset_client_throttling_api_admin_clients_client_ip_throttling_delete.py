from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.http_validation_error import HTTPValidationError
from ...models.reset_response import ResetResponse
from ...types import UNSET, Response, Unset


def _get_kwargs(
    client_ip: str,
    *,
    x_admin_token: None | str | Unset = UNSET,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}
    if not isinstance(x_admin_token, Unset):
        headers["x-admin-token"] = x_admin_token

    _kwargs: dict[str, Any] = {
        "method": "delete",
        "url": "/api/admin/clients/{client_ip}/throttling".format(
            client_ip=quote(str(client_ip), safe=""),
        ),
    }

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | HTTPValidationError | ResetResponse | None:
    if response.status_code == 200:
        response_200 = ResetResponse.from_dict(response.json())

        return response_200

    if response.status_code == 403:
        response_403 = ErrorResponse.from_dict(response.json())

        return response_403

    if response.status_code == 422:
        response_422 = HTTPValidationError.from_dict(response.json())

        return response_422

    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[ErrorResponse | HTTPValidationError | ResetResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    client_ip: str,
    *,
    client: AuthenticatedClient | Client,
    x_admin_token: None | str | Unset = UNSET,
) -> Response[ErrorResponse | HTTPValidationError | ResetResponse]:
    """Снять бан и обнулить счётчики частоты запросов

     Досрочно разблокировать клиента и очистить его счётчики нарушений.

    Args:
        client_ip (str): IP-адрес клиента.
        x_admin_token (None | str | Unset): Служебный токен администратора.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | HTTPValidationError | ResetResponse]
    """

    kwargs = _get_kwargs(
        client_ip=client_ip,
        x_admin_token=x_admin_token,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    client_ip: str,
    *,
    client: AuthenticatedClient | Client,
    x_admin_token: None | str | Unset = UNSET,
) -> ErrorResponse | HTTPValidationError | ResetResponse | None:
    """Снять бан и обнулить счётчики частоты запросов

     Досрочно разблокировать клиента и очистить его счётчики нарушений.

    Args:
        client_ip (str): IP-адрес клиента.
        x_admin_token (None | str | Unset): Служебный токен администратора.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | HTTPValidationError | ResetResponse
    """

    return sync_detailed(
        client_ip=client_ip,
        client=client,
        x_admin_token=x_admin_token,
    ).parsed


async def asyncio_detailed(
    client_ip: str,
    *,
    client: AuthenticatedClient | Client,
    x_admin_token: None | str | Unset = UNSET,
) -> Response[ErrorResponse | HTTPValidationError | ResetResponse]:
    """Снять бан и обнулить счётчики частоты запросов

     Досрочно разблокировать клиента и очистить его счётчики нарушений.

    Args:
        client_ip (str): IP-адрес клиента.
        x_admin_token (None | str | Unset): Служебный токен администратора.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | HTTPValidationError | ResetResponse]
    """

    kwargs = _get_kwargs(
        client_ip=client_ip,
        x_admin_token=x_admin_token,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    client_ip: str,
    *,
    client: AuthenticatedClient | Client,
    x_admin_token: None | str | Unset = UNSET,
) -> ErrorResponse | HTTPValidationError | ResetResponse | None:
    """Снять бан и обнулить счётчики частоты запросов

     Досрочно разблокировать клиента и очистить его счётчики нарушений.

    Args:
        client_ip (str): IP-адрес клиента.
        x_admin_token (None | str | Unset): Служебный токен администратора.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | HTTPValidationError | ResetResponse
    """

    return (
        await asyncio_detailed(
            client_ip=client_ip,
            client=client,
            x_admin_token=x_admin_token,
        )
    ).parsed
