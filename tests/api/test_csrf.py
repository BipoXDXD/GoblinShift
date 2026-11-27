"""CSRF nas mutações autenticadas por cookie (docs/GoblinShiftPlan.md, 3.6).

Cada recusa é parametrizada por toda rota mutável registrada na matriz de autorização: rota
nova entra sozinha. A mutação legítima de referência é o cancelamento de uma Run que não existe:
passar pelo CSRF dá 404, e não 403.
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx2 as httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.entrypoints.api.dependencies import current_time
from goblinshift.identity import REQUESTS_PER_MINUTE, RequestClass
from goblinshift.identity.tables import audit_event, web_session
from tests.api.session_support import open_session, session_headers
from tests.api.support import assert_problem
from tests.identity.support import new_key, new_user
from tests.security.authz_matrix import entries
from tests.security.path_params import fill_path_params

pytestmark = pytest.mark.integration

_MUTATING = {"POST", "PUT", "PATCH", "DELETE"}
CANCEL = "/api/v1/runs/{run_id}/cancel"
NOW = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)
# Passado o minuto de `LAST_SEEN_RESOLUTION`: um uso registrado aqui grava `last_seen_at`.
AFTER_A_MINUTE = NOW + timedelta(seconds=61)
MUTATIONS = [
    pytest.param(method, path, id=f"{method} {path}")
    for method, path, _rule in entries()
    if method in _MUTATING
]


def _send(client: TestClient, method: str, path: str, headers: dict[str, str]) -> httpx.Response:
    value = str(uuid4())
    return client.request(method, fill_path_params(path, lambda _name: value), headers=headers)


def _freeze(app: FastAPI, now: datetime) -> None:
    app.dependency_overrides[current_time] = lambda: now


def _last_seen_and_idle_expiry(
    factory: sessionmaker[Session], session_id: UUID
) -> tuple[datetime, datetime]:
    with factory() as session:
        row = session.execute(
            select(web_session.c.last_seen_at, web_session.c.idle_expires_at).where(
                web_session.c.id == session_id
            )
        ).one()
    return row.last_seen_at, row.idle_expires_at


def _csrf_failures(factory: sessionmaker[Session]) -> list[str]:
    with factory() as session:
        return list(
            session.execute(
                select(audit_event.c.outcome).where(audit_event.c.event_type == "CSRF_FAILED")
            ).scalars()
        )


@pytest.mark.parametrize(("method", "path"), MUTATIONS)
def test_mutacao_por_cookie_sem_x_csrf_token_403(
    app: FastAPI, client: TestClient, factory: sessionmaker[Session], method: str, path: str
) -> None:
    headers = session_headers(app, open_session(app, new_user(factory)))
    del headers["X-CSRF-Token"]

    response = _send(client, method, path, headers)

    body = assert_problem(response, 403, "CSRF_FAILED")
    assert "Conta de teste" not in response.text
    assert body["detail"]


@pytest.mark.parametrize(("method", "path"), MUTATIONS)
def test_mutacao_por_cookie_cross_site_e_recusada(
    app: FastAPI, client: TestClient, factory: sessionmaker[Session], method: str, path: str
) -> None:
    headers = session_headers(app, open_session(app, new_user(factory)))
    headers["Sec-Fetch-Site"] = "cross-site"

    assert_problem(_send(client, method, path, headers), 403, "CSRF_FAILED")


@pytest.mark.parametrize(("method", "path"), MUTATIONS)
def test_mutacao_por_cookie_com_origin_de_outra_origem_e_recusada(
    app: FastAPI, client: TestClient, factory: sessionmaker[Session], method: str, path: str
) -> None:
    headers = session_headers(app, open_session(app, new_user(factory)))
    headers["Origin"] = "https://evil.example"

    assert_problem(_send(client, method, path, headers), 403, "CSRF_FAILED")


@pytest.mark.parametrize(("method", "path"), MUTATIONS)
def test_mutacao_por_cookie_com_token_de_outra_sessao_e_recusada(
    app: FastAPI, client: TestClient, factory: sessionmaker[Session], method: str, path: str
) -> None:
    user_id = new_user(factory)
    mine, other = open_session(app, user_id), open_session(app, user_id)
    headers = session_headers(app, mine)
    # O atacante conhece um token válido, mas de outra sessão: cookie e header iguais.
    headers["Cookie"] = headers["Cookie"].replace(mine.csrf_token, other.csrf_token)
    headers["X-CSRF-Token"] = other.csrf_token

    assert_problem(_send(client, method, path, headers), 403, "CSRF_FAILED")


def test_header_diferente_do_cookie_e_recusado(
    app: FastAPI, client: TestClient, factory: sessionmaker[Session]
) -> None:
    user_id = new_user(factory)
    issued = open_session(app, user_id)
    headers = session_headers(app, issued, csrf_header=open_session(app, user_id).csrf_token)

    assert_problem(_send(client, "POST", CANCEL, headers), 403, "CSRF_FAILED")


def test_falha_de_csrf_e_auditada_mesmo_com_a_requisicao_recusada(
    app: FastAPI, client: TestClient, factory: sessionmaker[Session]
) -> None:
    headers = session_headers(app, open_session(app, new_user(factory)))
    del headers["X-CSRF-Token"]

    _send(client, "POST", CANCEL, headers)

    assert _csrf_failures(factory) == ["failure"]


def test_rajada_de_csrf_invalido_grava_uma_linha(
    app: FastAPI, client: TestClient, factory: sessionmaker[Session]
) -> None:
    # Uma linha por conta e minuto: a rajada não inunda a auditoria (#442, 50a).
    issued = open_session(app, new_user(factory), now=NOW)
    _freeze(app, NOW)
    headers = session_headers(app, issued)
    del headers["X-CSRF-Token"]

    statuses = [
        _send(client, "POST", CANCEL, headers).status_code
        for _ in range(REQUESTS_PER_MINUTE[RequestClass.COMMAND])
    ]

    assert statuses == [403] * REQUESTS_PER_MINUTE[RequestClass.COMMAND]
    assert _csrf_failures(factory) == ["failure"]


def test_mutacao_legitima_por_cookie_passa_pelo_csrf(
    app: FastAPI, client: TestClient, factory: sessionmaker[Session]
) -> None:
    headers = session_headers(app, open_session(app, new_user(factory)))

    response = _send(client, "POST", CANCEL, headers)

    assert_problem(response, 404, "NOT_FOUND")
    assert _csrf_failures(factory) == []


@pytest.mark.parametrize("header", ["Sec-Fetch-Site", "Origin"])
def test_cliente_sem_fetch_metadata_ou_origin_ainda_passa_com_o_token(
    app: FastAPI, client: TestClient, factory: sessionmaker[Session], header: str
) -> None:
    headers = session_headers(app, open_session(app, new_user(factory)))
    del headers[header]

    assert_problem(_send(client, "POST", CANCEL, headers), 404, "NOT_FOUND")


def test_leitura_por_cookie_nao_exige_csrf(
    app: FastAPI, client: TestClient, factory: sessionmaker[Session]
) -> None:
    headers = session_headers(app, open_session(app, new_user(factory)))
    del headers["X-CSRF-Token"]
    headers["Sec-Fetch-Site"] = "cross-site"

    assert client.get("/api/v1/me", headers=headers).status_code == 200


def test_bearer_nao_exige_csrf(client: TestClient, factory: sessionmaker[Session]) -> None:
    token = new_key(factory, new_user(factory)).token
    headers = {
        "Authorization": f"Bearer {token}",
        "Sec-Fetch-Site": "cross-site",
        "Origin": "https://evil.example",
    }

    assert_problem(_send(client, "POST", CANCEL, headers), 404, "NOT_FOUND")


# --- Ordem: identificar, contar, conferir o CSRF e só então registrar o uso ------------------


def test_post_same_site_recusado_nao_estende_a_sessao(
    app: FastAPI, client: TestClient, factory: sessionmaker[Session]
) -> None:
    issued = open_session(app, new_user(factory), now=NOW)
    _freeze(app, AFTER_A_MINUTE)
    headers = {**session_headers(app, issued), "Sec-Fetch-Site": "same-site"}
    del headers["X-CSRF-Token"]

    response = _send(client, "POST", CANCEL, headers)

    assert_problem(response, 403, "CSRF_FAILED")
    assert _last_seen_and_idle_expiry(factory, issued.session_id) == (
        NOW,
        NOW + timedelta(minutes=30),
    )


def test_mutacao_com_csrf_invalido_conta_no_limite_de_comandos(
    app: FastAPI, client: TestClient, factory: sessionmaker[Session]
) -> None:
    issued = open_session(app, new_user(factory), now=NOW)
    _freeze(app, AFTER_A_MINUTE)
    headers = session_headers(app, issued, csrf_header="x" * 87)

    for _ in range(REQUESTS_PER_MINUTE[RequestClass.COMMAND]):
        assert _send(client, "POST", CANCEL, headers).status_code == 403
    response = _send(client, "POST", CANCEL, headers)

    assert_problem(response, 429, "RATE_LIMITED")
    assert "retry-after" in response.headers


def test_mutacao_legitima_estende_a_sessao_depois_de_um_minuto(
    app: FastAPI, client: TestClient, factory: sessionmaker[Session]
) -> None:
    issued = open_session(app, new_user(factory), now=NOW)
    _freeze(app, AFTER_A_MINUTE)

    response = _send(client, "POST", CANCEL, session_headers(app, issued))

    assert_problem(response, 404, "NOT_FOUND")
    assert _last_seen_and_idle_expiry(factory, issued.session_id) == (
        AFTER_A_MINUTE,
        AFTER_A_MINUTE + timedelta(minutes=30),
    )
