"""Limite de requisições na borda HTTP: 429 `RATE_LIMITED` com `Retry-After` (1.7; ADR 028).

Por conta e por classe de rota (leituras 120, comandos 60 e criação de execução 10 por minuto).
A conta é a da credencial, nunca o IP. Envio e download de conteúdo ficam fora: valem as
transferências simultâneas (plano, "Upload em lote").
"""

import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import httpx2 as httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.entrypoints.api.dependencies import current_time
from goblinshift.identity.tables import request_window
from tests.api.files_support import create_upload, new_account, send_content
from tests.api.support import assert_problem

pytestmark = pytest.mark.integration

ME = "/api/v1/me"
RUNS = "/api/v1/runs"
# 12:00:15,2: a janela das 12:00 termina em 44,8 s, que o `Retry-After` arredonda para 45.
NOW = datetime(2026, 10, 14, 12, 0, 15, 200_000, tzinfo=UTC)

type Send = Callable[[TestClient, dict[str, str]], httpx.Response]


@pytest.fixture(autouse=True)
def frozen_clock(app: FastAPI) -> None:
    app.dependency_overrides[current_time] = lambda: NOW


def read(client: TestClient, auth: dict[str, str]) -> httpx.Response:
    return client.get(ME, headers=auth)


def command(client: TestClient, auth: dict[str, str]) -> httpx.Response:
    return client.post(f"{RUNS}/{uuid.uuid4()}/cancel", headers=auth)


def create_run(client: TestClient, auth: dict[str, str]) -> httpx.Response:
    """Validação sem efeito: conta como criação de execução, sem depender de uploads."""
    return client.post(
        f"{RUNS}?validate_only=true",
        json={"recipe_id": "csv-janitor", "recipe_version": 1, "parameters": {}},
        headers={**auth, "Idempotency-Key": uuid.uuid4().hex},
    )


def send_many(client: TestClient, auth: dict[str, str], send: Send, times: int) -> None:
    for _ in range(times):
        response = send(client, auth)
        assert response.status_code != 429, response.text


@pytest.mark.parametrize(
    ("send", "limit"),
    [(read, 120), (command, 60), (create_run, 10)],
    ids=["leitura", "comando", "criacao_de_execucao"],
)
def test_requisicao_acima_do_limite_429_rate_limited_com_retry_after(
    client: TestClient, factory: sessionmaker[Session], send: Send, limit: int
) -> None:
    owner, auth = new_account(factory)
    send_many(client, auth, send, limit)

    response = send(client, auth)

    body = assert_problem(response, 429, "RATE_LIMITED")
    assert response.headers["Retry-After"] == "45"
    assert str(owner) not in response.text
    assert "errors" not in body


def test_conta_limitada_continua_limitada_em_outro_ip(
    app: FastAPI, factory: sessionmaker[Session]
) -> None:
    _, auth = new_account(factory)
    with TestClient(app, client=("203.0.113.10", 40000)) as first_ip:
        send_many(first_ip, auth, create_run, 10)

    with TestClient(app, client=("198.51.100.20", 40000)) as second_ip:
        response = create_run(second_ip, auth)

    assert_problem(response, 429, "RATE_LIMITED")


def test_outra_conta_no_mesmo_ip_nao_e_limitada(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    _, limited = new_account(factory)
    _, other = new_account(factory)
    send_many(client, limited, create_run, 10)

    response = create_run(client, other)

    assert response.status_code != 429


def test_limite_de_uma_classe_nao_bloqueia_as_outras(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    _, auth = new_account(factory)
    send_many(client, auth, create_run, 10)

    assert read(client, auth).status_code == 200
    assert command(client, auth).status_code == 404


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (datetime(2026, 10, 14, 12, 0, 0, tzinfo=UTC), "60"),
        (datetime(2026, 10, 14, 12, 0, 59, tzinfo=UTC), "1"),
        # Menos de um segundo restante: arredonda para cima, nunca 0.
        (datetime(2026, 10, 14, 12, 0, 59, 999_000, tzinfo=UTC), "1"),
    ],
)
def test_retry_after_e_o_tempo_ate_o_fim_da_janela_pelo_relogio_injetado(
    app: FastAPI,
    client: TestClient,
    factory: sessionmaker[Session],
    now: datetime,
    expected: str,
) -> None:
    app.dependency_overrides[current_time] = lambda: now
    _, auth = new_account(factory)
    send_many(client, auth, create_run, 10)

    response = create_run(client, auth)

    assert response.status_code == 429
    assert response.headers["Retry-After"] == expected


def test_depois_do_retry_after_a_conta_volta_a_ser_atendida(
    app: FastAPI, client: TestClient, factory: sessionmaker[Session]
) -> None:
    _, auth = new_account(factory)
    send_many(client, auth, create_run, 10)
    refused = create_run(client, auth)
    assert refused.status_code == 429

    later = NOW + timedelta(seconds=int(refused.headers["Retry-After"]))
    app.dependency_overrides[current_time] = lambda: later

    assert create_run(client, auth).status_code != 429


def test_requisicao_sem_credencial_nao_conta(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    response = client.get(ME)

    assert response.status_code == 401
    assert windows_total(factory) == 0


def test_envio_de_conteudo_nao_conta_no_limite_por_minuto(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    _, auth = new_account(factory)
    item = create_upload(client, auth)  # um comando

    send_content(client, auth, item, b"a,b")

    assert windows_total(factory) == 1


def windows_total(factory: sessionmaker[Session]) -> int:
    with factory() as session:
        total: int = session.execute(
            select(func.coalesce(func.sum(request_window.c.request_count), 0))
        ).scalar_one()
    return total
