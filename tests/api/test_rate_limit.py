"""Limite de requisições na borda HTTP: 429 `RATE_LIMITED` com `Retry-After` (1.7; ADR 028).

Por conta, canal e classe de rota (leituras 120, comandos 60 e criação de execução 10 por
minuto). A conta é a da credencial, nunca o IP. O canal é o `Principal.via`: as chaves de API da
conta dividem um balde, e a sessão web tem o seu, para nenhuma chave trancar o titular fora da
sessão que a revoga (#442, 49a). Envio e download de conteúdo ficam fora: valem as transferências
simultâneas (plano, "Upload em lote").
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
from goblinshift.identity import Scope
from goblinshift.identity.tables import channel_request_window
from tests.api.files_support import bearer, create_upload, new_account, send_content
from tests.api.session_support import open_session, session_headers
from tests.api.support import assert_problem
from tests.identity.support import new_key, new_user

pytestmark = pytest.mark.integration

ME = "/api/v1/me"
RUNS = "/api/v1/runs"
CATALOG = "/api/v1/catalog"
UPLOADS = "/api/v1/uploads"
KEYS = "/api/v1/api-keys"
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


def send_many(
    client: TestClient, auth: dict[str, str], send: Send, times: int, accepted: int = 200
) -> None:
    """Dentro do limite, cada requisição recebe a resposta normal da rota, não só "não 429"."""
    for _ in range(times):
        response = send(client, auth)
        assert response.status_code == accepted, response.text


@pytest.mark.parametrize(
    ("send", "limit", "accepted"),
    # O comando cancela uma execução inexistente: a resposta normal é 404.
    [(read, 120, 200), (command, 60, 404), (create_run, 10, 200)],
    ids=["leitura", "comando", "criacao_de_execucao"],
)
def test_requisicao_acima_do_limite_429_rate_limited_com_retry_after(
    client: TestClient, factory: sessionmaker[Session], send: Send, limit: int, accepted: int
) -> None:
    owner, auth = new_account(factory)
    send_many(client, auth, send, limit, accepted)

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

    assert response.status_code == 200, response.text


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

    assert create_run(client, auth).status_code == 200


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
            select(func.coalesce(func.sum(channel_request_window.c.request_count), 0))
        ).scalar_one()
    return total


# --- Canais: sessão web e chaves de API (#442, 49a) ------------------------------------------


def session_auth(app: FastAPI, owner: uuid.UUID) -> dict[str, str]:
    """Cookie de sessão real com os headers de uma mutação legítima da SPA (CSRF incluído)."""
    return session_headers(app, open_session(app, owner, now=NOW))


def key_auth(
    factory: sessionmaker[Session], owner: uuid.UUID, scopes: frozenset[Scope]
) -> tuple[uuid.UUID, dict[str, str]]:
    issued = new_key(factory, owner, scopes=scopes, now=NOW)
    return issued.id, bearer(issued.token)


def read_catalog(client: TestClient, auth: dict[str, str]) -> httpx.Response:
    return client.get(CATALOG, headers=auth)


def delete_unknown_upload(client: TestClient, auth: dict[str, str]) -> httpx.Response:
    return client.delete(f"{UPLOADS}/{uuid.uuid4()}", headers=auth)


def test_chave_que_esgota_a_conta_nao_tranca_a_sessao(
    app: FastAPI, client: TestClient, factory: sessionmaker[Session]
) -> None:
    owner = new_user(factory)
    key_id, key = key_auth(factory, owner, frozenset({Scope.FILES_WRITE}))
    send_many(client, key, read_catalog, 120)
    send_many(client, key, delete_unknown_upload, 60, accepted=404)
    assert_problem(read_catalog(client, key), 429, "RATE_LIMITED")
    assert_problem(delete_unknown_upload(client, key), 429, "RATE_LIMITED")
    titular = session_auth(app, owner)

    listed = client.get(KEYS, headers=titular)
    revoked = client.delete(f"{KEYS}/{key_id}", headers=titular)

    assert listed.status_code == 200, listed.text
    assert str(key_id) in {item["id"] for item in listed.json()["data"]}
    assert revoked.status_code == 204, revoked.text
    assert_problem(read_catalog(client, key), 401, "AUTHENTICATION_REQUIRED")


def test_chave_esgotada_nao_bloqueia_a_sessao_da_mesma_conta(
    app: FastAPI, client: TestClient, factory: sessionmaker[Session]
) -> None:
    owner = new_user(factory)
    _, key = key_auth(factory, owner, frozenset(Scope))
    send_many(client, key, create_run, 10)
    assert create_run(client, key).status_code == 429

    response = create_run(client, session_auth(app, owner))

    assert response.status_code == 200, response.text


def test_sessao_esgotada_nao_bloqueia_a_chave(
    app: FastAPI, client: TestClient, factory: sessionmaker[Session]
) -> None:
    owner = new_user(factory)
    titular = session_auth(app, owner)
    send_many(client, titular, create_run, 10)
    assert create_run(client, titular).status_code == 429
    _, key = key_auth(factory, owner, frozenset(Scope))

    response = create_run(client, key)

    assert response.status_code == 200, response.text


def test_sessao_continua_limitada_no_proprio_canal(
    app: FastAPI, client: TestClient, factory: sessionmaker[Session]
) -> None:
    titular = session_auth(app, new_user(factory))
    send_many(client, titular, command, 60, accepted=404)

    response = command(client, titular)

    assert_problem(response, 429, "RATE_LIMITED")


def test_cinco_chaves_dividem_o_mesmo_balde(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    # O canal é `api_key`, não a chave: emitir mais chaves não multiplica o limite (D1 = C).
    owner = new_user(factory)
    _, first = key_auth(factory, owner, frozenset(Scope))
    _, second = key_auth(factory, owner, frozenset(Scope))
    send_many(client, first, command, 30, accepted=404)
    send_many(client, second, command, 30, accepted=404)

    response = command(client, second)

    assert_problem(response, 429, "RATE_LIMITED")
