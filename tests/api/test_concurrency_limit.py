"""Teto de requisições simultâneas da API: acima dele, 503 problem+json com Retry-After (2.3).

O uvicorn também tem um teto (`--limit-concurrency`), mas responde 503 `text/plain`, sem
Retry-After e antes do ASGI. O middleware recusa um pouco antes, no formato do contrato, e o do
uvicorn fica como última barreira. A aplicação é a real, sem banco; as rotas de teste esperam um
evento para segurar a vaga o tempo que o teste quiser.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import anyio
import httpx2 as httpx
import pytest
from fastapi import APIRouter

from goblinshift.entrypoints.api.app import create_app
from goblinshift.entrypoints.api.concurrency import MAX_CONCURRENT_REQUESTS
from goblinshift.platform.settings import Settings
from tests.api.support import assert_problem

BLOCKING = "/api/v1/_test/blocking"
FAST = "/api/v1/_test/fast"
RAISING = "/api/v1/_test/raising"


class Gate:
    """Segura as requisições na rota até `release` e avisa quando `expected` delas entraram."""

    def __init__(self, expected: int) -> None:
        self.expected = expected
        self.inside = 0
        self.all_entered = anyio.Event()
        self.release = anyio.Event()


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def gate() -> Gate:
    return Gate(MAX_CONCURRENT_REQUESTS)


@pytest.fixture
async def client(gate: Gate) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(Settings(environment="test", db_port=1, db_connect_timeout_s=1))
    router = APIRouter(prefix="/api/v1/_test")

    @router.get("/blocking")
    async def blocking() -> dict[str, bool]:
        gate.inside += 1
        if gate.inside == gate.expected:
            gate.all_entered.set()
        await gate.release.wait()
        return {"ok": True}

    @router.get("/fast")
    async def fast() -> dict[str, bool]:
        return {"ok": True}

    @router.get("/raising")
    async def raising() -> None:
        raise RuntimeError("falha da rota")

    app.include_router(router)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@asynccontextmanager
async def saturated(client: httpx.AsyncClient, gate: Gate) -> AsyncIterator[list[httpx.Response]]:
    """Ocupa todas as vagas com requisições presas e as solta ao sair."""
    held: list[httpx.Response] = []

    async def hit() -> None:
        held.append(await client.get(BLOCKING))

    async with anyio.create_task_group() as group:
        for _ in range(MAX_CONCURRENT_REQUESTS):
            group.start_soon(hit)
        with anyio.fail_after(5):
            await gate.all_entered.wait()
        try:
            yield held
        finally:
            gate.release.set()


@pytest.mark.anyio
async def test_29a_requisicao_simultanea_recebe_503_problem_json(
    client: httpx.AsyncClient, gate: Gate
) -> None:
    async with saturated(client, gate) as held:
        refused = await client.get(FAST)

    assert_problem(refused, 503, "SERVICE_UNAVAILABLE")
    assert int(refused.headers["Retry-After"]) >= 1
    assert len(held) == MAX_CONCURRENT_REQUESTS
    assert all(response.status_code == 200 for response in held)


@pytest.mark.anyio
async def test_vaga_volta_depois_da_resposta(client: httpx.AsyncClient, gate: Gate) -> None:
    async with saturated(client, gate):
        pass

    response = await client.get(FAST)

    assert response.status_code == 200


@pytest.mark.anyio
async def test_rota_que_levanta_tambem_devolve_a_vaga(client: httpx.AsyncClient) -> None:
    for _ in range(MAX_CONCURRENT_REQUESTS):
        assert (await client.get(RAISING)).status_code == 500

    response = await client.get(FAST)

    assert response.status_code == 200


@pytest.mark.anyio
async def test_livez_responde_com_a_api_saturada(client: httpx.AsyncClient, gate: Gate) -> None:
    async with saturated(client, gate):
        response = await client.get("/livez")

    assert response.status_code == 200


@pytest.mark.anyio
async def test_readyz_responde_pela_propria_sonda_com_a_api_saturada(
    client: httpx.AsyncClient, gate: Gate
) -> None:
    # O /readyz tem cache e zona própria no Nginx; o healthcheck não pode tirar do ar uma API
    # saturada, mas viva. Sem banco, a própria sonda responde 503, e não o middleware.
    async with saturated(client, gate):
        response = await client.get("/readyz")

    assert "simultâneas" not in response.text
    assert response.headers.get("Retry-After") != "1"
