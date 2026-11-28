"""Teto de bytes nas respostas dos provedores de login (`CappedTransport`)."""

import asyncio
import gzip
from collections.abc import AsyncIterator

import httpx2 as httpx
import pytest
from fastapi.testclient import TestClient

from goblinshift.entrypoints.api.app import create_app
from goblinshift.entrypoints.api.oauth_http import (
    MAX_PROVIDER_RESPONSE_BYTES,
    CappedTransport,
    ProviderResponseTooLargeError,
)
from goblinshift.platform.settings import Settings

URL = "https://provedor.test/token"


def fetch(handler: httpx.MockTransport) -> httpx.Response:
    async def run() -> httpx.Response:
        transport = CappedTransport(handler, MAX_PROVIDER_RESPONSE_BYTES)
        async with httpx.AsyncClient(transport=transport) as client:
            return await client.get(URL)

    return asyncio.run(run())


@pytest.mark.parametrize(
    ("size", "accepted"),
    [(MAX_PROVIDER_RESPONSE_BYTES, True), (MAX_PROVIDER_RESPONSE_BYTES + 1, False)],
    ids=["no-teto-exato", "um-byte-alem"],
)
def test_teto_de_bytes_da_resposta(size: int, accepted: bool) -> None:
    # Corpo em fluxo, sem `Content-Length`: com `bytes`, o httpx já leria o corpo no mock.
    body = b"x" * size

    async def chunks() -> AsyncIterator[bytes]:
        yield body[:1024]
        yield body[1024:]

    handler = httpx.MockTransport(lambda request: httpx.Response(200, content=chunks()))

    if accepted:
        assert fetch(handler).content == body
    else:
        with pytest.raises(ProviderResponseTooLargeError):
            fetch(handler)


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


def test_resposta_comprimida_e_recusada_mesmo_pequena() -> None:
    # O httpx descomprime depois do transport: 1 KiB de gzip viraria 4 MiB em memória.
    bomb = gzip.compress(b"\0" * (4 * 1024 * 1024))
    assert len(bomb) < MAX_PROVIDER_RESPONSE_BYTES
    closed: list[bool] = []

    class Body(httpx.AsyncByteStream):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            yield bomb

        async def aclose(self) -> None:
            closed.append(True)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"Content-Encoding": "gzip"}, stream=Body())

    with pytest.raises(ProviderResponseTooLargeError):
        fetch(httpx.MockTransport(handler))
    assert closed == [True]


def test_resposta_identity_declarada_passa() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"Content-Encoding": "identity"}, content=b"{}")

    assert fetch(httpx.MockTransport(handler)).content == b"{}"


def test_fechar_o_cliente_nao_fecha_o_transport_compartilhado() -> None:
    # O Authlib abre e fecha um cliente por chamada, todos com o mesmo transport; fechar o pool
    # derrubaria a conexão de outro login em andamento.
    closed: list[bool] = []

    class Inner(httpx.MockTransport):
        async def aclose(self) -> None:
            closed.append(True)

    fetch(Inner(lambda request: httpx.Response(200, content=b"{}")))

    assert closed == []


def test_parada_da_api_fecha_o_pool_http_do_login() -> None:
    closed: list[bool] = []

    class Inner(httpx.AsyncBaseTransport):
        async def aclose(self) -> None:
            closed.append(True)

    app = create_app(Settings(environment="test", db_port=1, db_connect_timeout_s=1))
    app.state.login_transport = Inner()

    with TestClient(app):
        assert closed == []

    assert closed == [True]
