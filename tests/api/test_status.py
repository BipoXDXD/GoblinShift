"""`GET /api/v1/status`: estado do serviço sem login e sem contagens (D9 do plano de paridade).

Sem Docker: a leitura do banco é trocada por um valor fixo, e o banco fora é a porta sem banco do
`offline_client`. O caminho com PostgreSQL real fica no teste `integration` do fim.
"""

from collections.abc import Iterator
from contextlib import nullcontext
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from goblinshift.entrypoints.api import status as status_route
from goblinshift.entrypoints.api.app import create_app
from goblinshift.entrypoints.api.dependencies import get_session
from goblinshift.platform.settings import Settings
from goblinshift.runs.service_status import ProcessingLevel, QueueLevel, ServiceStatus
from tests.api.support import assert_problem

STATUS = "/api/v1/status"
CHECKED_AT = datetime(2026, 11, 28, 12, 30, tzinfo=UTC)


class _FakeSession:
    """Basta para a rota abrir a transação; a leitura em si é trocada no teste."""

    def begin(self) -> nullcontext[None]:
        return nullcontext()


def _fake_session() -> Iterator[_FakeSession]:
    yield _FakeSession()


@pytest.fixture
def read_calls(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    calls: list[object] = []

    def fake_read(session: object) -> ServiceStatus:
        calls.append(session)
        return ServiceStatus(
            processing=ProcessingLevel.SLOW, queue=QueueLevel.BUSY, checked_at=CHECKED_AT
        )

    monkeypatch.setattr(status_route, "read_service_status", fake_read)
    return calls


@pytest.fixture
def status_client(read_calls: list[object]) -> Iterator[TestClient]:
    app = create_app(Settings(environment="test", db_port=1, db_connect_timeout_s=1))
    app.dependency_overrides[get_session] = _fake_session
    with TestClient(app) as client:
        yield client


def _numbers(value: Any) -> list[int | float]:
    """Todo número do JSON, em qualquer profundidade (bool não conta)."""
    if isinstance(value, dict):
        return [n for item in value.values() for n in _numbers(item)]
    if isinstance(value, list):
        return [n for item in value for n in _numbers(item)]
    if isinstance(value, int | float) and not isinstance(value, bool):
        return [value]
    return []


def test_responde_sem_sessao_e_sem_cookie(
    status_client: TestClient, read_calls: list[object]
) -> None:
    response = status_client.get(STATUS)

    assert response.status_code == 200, response.text
    assert response.json() == {
        "api": "up",
        "processing": "slow",
        "queue": "busy",
        "checked_at": "2026-11-28T12:30:00Z",
    }
    assert "set-cookie" not in response.headers
    assert len(read_calls) == 1


def test_o_corpo_nao_tem_numero_alem_da_data(status_client: TestClient) -> None:
    assert _numbers(status_client.get(STATUS).json()) == []


def test_no_store(status_client: TestClient) -> None:
    assert status_client.get(STATUS).headers["cache-control"] == "no-store"


def test_query_fora_do_contrato_responde_422_sem_credencial(status_client: TestClient) -> None:
    assert_problem(status_client.get(f"{STATUS}?detalhe=1"), 422, "VALIDATION_FAILED")


def test_banco_fora_responde_503_com_retry_after(offline_client: TestClient) -> None:
    response = offline_client.get(STATUS)

    assert_problem(response, 503, "SERVICE_UNAVAILABLE")
    assert response.headers["retry-after"] == "5"


def test_contrato_publica_a_operacao(offline_client: TestClient) -> None:
    app: FastAPI = offline_client.app  # type: ignore[assignment]
    operation = app.openapi()["paths"][STATUS]["get"]

    assert operation["operationId"] == "getServiceStatus"


@pytest.mark.integration
def test_banco_real_responde_sem_credencial(client: TestClient) -> None:
    response = client.get(STATUS)

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["api"], body["processing"], body["queue"]) == ("up", "normal", "free")
    datetime.fromisoformat(body["checked_at"])
