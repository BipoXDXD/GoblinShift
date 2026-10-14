"""CSRF nas mutações autenticadas por cookie (docs/GoblinShiftPlan.md, 3.6).

Cada recusa é parametrizada por toda rota mutável registrada na matriz de autorização: rota
nova entra sozinha. A mutação legítima de referência é o cancelamento de uma Run que não existe:
passar pelo CSRF dá 404, e não 403.
"""

import re
from uuid import uuid4

import httpx2 as httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.identity.tables import audit_event
from tests.api.session_support import open_session, session_headers
from tests.api.support import assert_problem
from tests.identity.support import new_key, new_user
from tests.security.authz_matrix import entries

pytestmark = pytest.mark.integration

_MUTATING = {"POST", "PUT", "PATCH", "DELETE"}
_PATH_PARAM = re.compile(r"\{[a-z_]+\}")
CANCEL = "/api/v1/runs/{run_id}/cancel"
MUTATIONS = [
    pytest.param(method, path, id=f"{method} {path}")
    for method, path, rule in entries()
    if method in _MUTATING and rule.pending is None
]


def _send(client: TestClient, method: str, path: str, headers: dict[str, str]) -> httpx.Response:
    return client.request(method, _PATH_PARAM.sub(str(uuid4()), path), headers=headers)


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
