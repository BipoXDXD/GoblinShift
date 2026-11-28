"""Teto de bytes nas respostas dos provedores de login (`CappedTransport`)."""

import asyncio
from collections.abc import AsyncIterator

import httpx2 as httpx
import pytest

from goblinshift.entrypoints.api.oauth_http import (
    MAX_PROVIDER_RESPONSE_BYTES,
    CappedTransport,
    ProviderResponseTooLargeError,
)
from goblinshift.identity.providers import MAX_GITHUB_USER_BYTES

URL = "https://provedor.test/token"


def fetch(handler: httpx.MockTransport) -> httpx.Response:
    async def run() -> httpx.Response:
        transport = CappedTransport(handler, MAX_PROVIDER_RESPONSE_BYTES)
        async with httpx.AsyncClient(transport=transport) as client:
            return await client.get(URL)

    return asyncio.run(run())


def test_resposta_no_teto_exato_passa() -> None:
    body = b"x" * MAX_PROVIDER_RESPONSE_BYTES

    response = fetch(httpx.MockTransport(lambda request: httpx.Response(200, content=body)))

    assert response.content == body


def test_um_byte_alem_do_teto_e_recusado() -> None:
    body = b"x" * (MAX_PROVIDER_RESPONSE_BYTES + 1)

    with pytest.raises(ProviderResponseTooLargeError):
        fetch(httpx.MockTransport(lambda request: httpx.Response(200, content=body)))


def test_corpo_sem_content_length_para_de_ler_no_teto() -> None:
    served: list[int] = []

    async def chunks() -> AsyncIterator[bytes]:
        for index in range(16):
            served.append(index)
            yield b"x" * (64 * 1024)

    with pytest.raises(ProviderResponseTooLargeError):
        fetch(httpx.MockTransport(lambda request: httpx.Response(200, content=chunks())))
    assert len(served) <= 2


def test_content_length_acima_do_teto_recusa_sem_ler() -> None:
    read: list[bool] = []

    async def body() -> AsyncIterator[bytes]:
        read.append(True)
        yield b"{}"

    def handler(request: httpx.Request) -> httpx.Response:
        declared = {"Content-Length": str(MAX_PROVIDER_RESPONSE_BYTES + 1)}
        return httpx.Response(200, headers=declared, content=body())

    with pytest.raises(ProviderResponseTooLargeError):
        fetch(httpx.MockTransport(handler))
    assert read == []


def test_pede_o_corpo_sem_compressao() -> None:
    # Com gzip, o teto contaria bytes comprimidos e um corpo pequeno viraria megabytes.
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["Accept-Encoding"])
        return httpx.Response(200, content=b"{}")

    fetch(httpx.MockTransport(handler))

    assert seen == ["identity"]


def test_fechar_o_cliente_nao_fecha_o_transport_compartilhado() -> None:
    # O Authlib abre e fecha um cliente por chamada, todos com o mesmo transport; fechar o pool
    # derrubaria a conexão de outro login em andamento.
    closed: list[bool] = []

    class Inner(httpx.MockTransport):
        async def aclose(self) -> None:
            closed.append(True)

    fetch(Inner(lambda request: httpx.Response(200, content=b"{}")))

    assert closed == []


def test_teto_do_transport_e_o_mesmo_do_usuario_do_github() -> None:
    assert MAX_PROVIDER_RESPONSE_BYTES == MAX_GITHUB_USER_BYTES
