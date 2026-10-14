"""Envio por streaming: escritor único, vaga ocupada durante a transferência e prazos (1.7, 3.6).

Estes testes usam o `ASGITransport` do httpx, que entrega o corpo à aplicação pedaço por pedaço
conforme o gerador do cliente avança; o `TestClient` lê o corpo inteiro antes de chamar a rota.
"""

import hashlib
from collections.abc import AsyncIterator, Iterator
from datetime import timedelta
from typing import Any

import anyio
import httpx2 as httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

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
    assert sync_client.get(f"{UPLOADS}/{item['id']}", headers=auth).json()["state"] == "failed"
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

    async def trickle() -> AsyncIterator[bytes]:
        for _ in range(100):
            await anyio.sleep(0.05)
            yield b"x"

    response = await put_content(async_client, auth, item, trickle())

    assert_problem(response, 408, "REQUEST_TIMEOUT")
    assert sync_client.get(f"{UPLOADS}/{item['id']}", headers=auth).json()["state"] == "failed"
