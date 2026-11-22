"""Envio por streaming: escritor único, vaga ocupada durante a transferência e prazos (1.7, 3.6).

Estes testes usam o `ASGITransport` do httpx, que entrega o corpo à aplicação pedaço por pedaço
conforme o gerador do cliente avança; o `TestClient` lê o corpo inteiro antes de chamar a rota.
"""

import hashlib
import json
from collections.abc import AsyncIterator, Iterator
from datetime import timedelta
from typing import Any
from urllib.parse import urlsplit

import anyio
import httpx2 as httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker
from starlette.types import Message, Scope

from goblinshift.entrypoints.api.transfers import TransferTimeouts
from tests.api.files_support import (
    OCTET_STREAM,
    UPLOADS,
    create_upload,
    idle_in_transaction,
    new_account,
    slot_count,
)
from tests.api.support import assert_problem

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

WAIT_LIMIT_S = 10
ME = "/api/v1/me"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def sync_client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as client:
        yield client


@pytest.fixture
async def async_client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


class PausedBody:
    """Corpo que envia o primeiro pedaço e espera `resume` antes de enviar o resto.

    `first_chunk_taken` só é sinalizado quando a rota pede o segundo pedaço, ou seja, depois de
    o servidor ter reservado o escritor e começado a gravar.
    """

    def __init__(self, first: bytes, rest: bytes) -> None:
        self.first = first
        self.rest = rest
        self.first_chunk_taken = anyio.Event()
        self.resume = anyio.Event()

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield self.first
        self.first_chunk_taken.set()
        await self.resume.wait()
        yield self.rest


async def put_content(
    client: httpx.AsyncClient, auth: dict[str, str], item: dict[str, Any], body: Any
) -> httpx.Response:
    return await client.put(item["content"]["url"], content=body, headers={**auth, **OCTET_STREAM})


async def trickle(data: bytes, interval_s: float) -> AsyncIterator[bytes]:
    """Um byte por vez, a cada `interval_s`."""
    for byte in data:
        await anyio.sleep(interval_s)
        yield bytes([byte])


def state_of(client: TestClient, auth: dict[str, str], item: dict[str, Any]) -> str:
    state: str = client.get(f"{UPLOADS}/{item['id']}", headers=auth).json()["state"]
    return state


def reserved_bytes(client: TestClient, auth: dict[str, str]) -> int:
    """Bytes que a conta tem reservados na cota de armazenamento, como o `/me` publica."""
    reserved: int = client.get(ME, headers=auth).json()["usage"]["storage_reserved_bytes"]
    return reserved


async def test_dois_envios_concorrentes_no_mesmo_upload_um_recebe_409(
    async_client: httpx.AsyncClient, sync_client: TestClient, factory: sessionmaker[Session]
) -> None:
    _, auth = new_account(factory)
    item = create_upload(sync_client, auth, size=6)
    slow = PausedBody(b"abc", b"def")
    responses: dict[str, httpx.Response] = {}

    async def first_writer() -> None:
        responses["first"] = await put_content(async_client, auth, item, slow)

    async with anyio.create_task_group() as group:
        group.start_soon(first_writer)
        with anyio.fail_after(WAIT_LIMIT_S):
            await slow.first_chunk_taken.wait()
        responses["second"] = await put_content(async_client, auth, item, b"zzzzzz")
        slow.resume.set()

    assert_problem(responses["second"], 409, "STATE_CONFLICT")
    assert responses["first"].status_code == 200, responses["first"].text
    assert responses["first"].json()["sha256"] == hashlib.sha256(b"abcdef").hexdigest()


async def test_envio_em_andamento_ocupa_a_vaga_sem_transacao_aberta(
    async_client: httpx.AsyncClient, sync_client: TestClient, factory: sessionmaker[Session]
) -> None:
    _, auth = new_account(factory)
    items = [create_upload(sync_client, auth, size=2) for _ in range(3)]
    bodies = [PausedBody(b"a", b"b") for _ in range(2)]
    responses: list[httpx.Response] = []

    async def writer(item: dict[str, Any], body: PausedBody) -> None:
        responses.append(await put_content(async_client, auth, item, body))

    async with anyio.create_task_group() as group:
        for item, body in zip(items, bodies, strict=False):
            group.start_soon(writer, item, body)
        with anyio.fail_after(WAIT_LIMIT_S):
            for body in bodies:
                await body.first_chunk_taken.wait()
        third = await put_content(async_client, auth, items[2], b"ab")
        occupied = await anyio.to_thread.run_sync(slot_count, factory)
        idle_transactions = await anyio.to_thread.run_sync(idle_in_transaction, factory)
        for body in bodies:
            body.resume.set()

    assert_problem(third, 429, "RATE_LIMITED")
    assert occupied == 2
    assert idle_transactions == 0
    assert [response.status_code for response in responses] == [200, 200]
    assert await anyio.to_thread.run_sync(slot_count, factory) == 0


async def test_envio_parado_alem_do_prazo_sem_progresso_falha_e_libera_a_vaga(
    app: FastAPI,
    async_client: httpx.AsyncClient,
    sync_client: TestClient,
    factory: sessionmaker[Session],
) -> None:
    # Prazo total longo: só o prazo sem progresso pode encerrar o envio dentro do teste.
    app.state.transfer_timeouts = TransferTimeouts(
        total=timedelta(minutes=10), idle=timedelta(milliseconds=200)
    )
    _, auth = new_account(factory)
    item = create_upload(sync_client, auth, size=2)
    stalled = PausedBody(b"a", b"b")

    with anyio.fail_after(WAIT_LIMIT_S):
        response = await put_content(async_client, auth, item, stalled)
    stalled.resume.set()

    assert_problem(response, 408, "REQUEST_TIMEOUT")
    assert state_of(sync_client, auth, item) == "failed"
    assert await anyio.to_thread.run_sync(slot_count, factory) == 0


async def test_envio_que_passa_do_prazo_total_falha_mesmo_com_progresso(
    app: FastAPI,
    async_client: httpx.AsyncClient,
    sync_client: TestClient,
    factory: sessionmaker[Session],
) -> None:
    app.state.transfer_timeouts = TransferTimeouts(
        total=timedelta(milliseconds=300), idle=timedelta(seconds=WAIT_LIMIT_S)
    )
    _, auth = new_account(factory)
    item = create_upload(sync_client, auth, size=100)

    response = await put_content(async_client, auth, item, trickle(b"x" * 100, 0.05))

    assert_problem(response, 408, "REQUEST_TIMEOUT")
    assert state_of(sync_client, auth, item) == "failed"


async def test_envio_abaixo_do_piso_recebe_408_antes_do_prazo_total_e_libera_a_vaga(
    app: FastAPI,
    async_client: httpx.AsyncClient,
    sync_client: TestClient,
    factory: sessionmaker[Session],
) -> None:
    # Prazos total e sem progresso longos: só o piso de vazão pode encerrar o envio no teste.
    app.state.transfer_timeouts = TransferTimeouts(
        total=timedelta(minutes=10),
        idle=timedelta(seconds=WAIT_LIMIT_S),
        min_rate_bytes_per_s=1000,
        min_rate_grace=timedelta(milliseconds=200),
    )
    _, auth = new_account(factory)
    item = create_upload(sync_client, auth, size=100)
    assert reserved_bytes(sync_client, auth) == 64 * 1024  # o piso de reserva da intenção

    with anyio.fail_after(WAIT_LIMIT_S):
        response = await put_content(async_client, auth, item, trickle(b"x" * 100, 0.05))

    assert_problem(response, 408, "REQUEST_TIMEOUT")
    assert state_of(sync_client, auth, item) == "failed"
    assert await anyio.to_thread.run_sync(slot_count, factory) == 0
    assert reserved_bytes(sync_client, auth) == 0


async def test_envio_lento_mas_acima_do_piso_passa(
    app: FastAPI,
    async_client: httpx.AsyncClient,
    sync_client: TestClient,
    factory: sessionmaker[Session],
) -> None:
    app.state.transfer_timeouts = TransferTimeouts(
        total=timedelta(minutes=10),
        idle=timedelta(seconds=WAIT_LIMIT_S),
        min_rate_bytes_per_s=10,
        min_rate_grace=timedelta(milliseconds=200),
    )
    _, auth = new_account(factory)
    content = b"0123456789abcdefghij"
    item = create_upload(sync_client, auth, size=len(content))

    # 20 B/s por 1 s, o dobro do piso.
    with anyio.fail_after(WAIT_LIMIT_S):
        response = await put_content(async_client, auth, item, trickle(content, 0.05))

    assert response.status_code == 200, response.text
    assert response.json()["sha256"] == hashlib.sha256(content).hexdigest()


async def test_pausa_antes_do_primeiro_byte_conta_para_o_piso(
    app: FastAPI,
    async_client: httpx.AsyncClient,
    sync_client: TestClient,
    factory: sessionmaker[Session],
) -> None:
    app.state.transfer_timeouts = TransferTimeouts(
        total=timedelta(minutes=10),
        idle=timedelta(seconds=WAIT_LIMIT_S),
        min_rate_bytes_per_s=1000,
        min_rate_grace=timedelta(milliseconds=200),
    )
    _, auth = new_account(factory)
    item = create_upload(sync_client, auth, size=1)

    # Parado 300 ms e depois 1 byte: a média desde o início da leitura fica bem abaixo do piso.
    with anyio.fail_after(WAIT_LIMIT_S):
        response = await put_content(async_client, auth, item, trickle(b"x", 0.3))

    assert_problem(response, 408, "REQUEST_TIMEOUT")
    assert state_of(sync_client, auth, item) == "failed"
    assert await anyio.to_thread.run_sync(slot_count, factory) == 0


async def test_cliente_que_desconecta_no_meio_do_envio_recebe_400_e_o_upload_falha(
    app: FastAPI, sync_client: TestClient, factory: sessionmaker[Session]
) -> None:
    # Chamada ASGI direta: só assim o servidor recebe `http.disconnect` no meio do corpo.
    _, auth = new_account(factory)
    item = create_upload(sync_client, auth, size=6)
    path = urlsplit(item["content"]["url"]).path
    headers = {**auth, **OCTET_STREAM, "Content-Length": "6"}
    scope: Scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "PUT",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [(name.lower().encode(), value.encode()) for name, value in headers.items()],
        "client": ("127.0.0.1", 1),
        "server": ("testserver", 80),
    }
    incoming = iter([{"type": "http.request", "body": b"abc", "more_body": True}])
    sent: list[Message] = []

    async def receive() -> Message:
        return next(incoming, {"type": "http.disconnect"})

    async def send(message: Message) -> None:
        sent.append(message)

    with anyio.fail_after(WAIT_LIMIT_S):
        await app(scope, receive, send)

    start = next(message for message in sent if message["type"] == "http.response.start")
    body = json.loads(b"".join(message.get("body", b"") for message in sent))
    assert start["status"] == 400
    assert (body["code"], body["detail"]) == ("BAD_REQUEST", "O envio foi interrompido.")
    assert state_of(sync_client, auth, item) == "failed"
    assert await anyio.to_thread.run_sync(slot_count, factory) == 0
