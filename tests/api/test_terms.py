"""Aceite dos termos na API: bloqueio até o aceite e `POST /api/v1/me/terms:accept` (1.8; ADR 029).

Conta do login sem o aceite da versão vigente recebe 403 `TERMS_NOT_ACCEPTED` em toda rota
autenticada, salvo as que leem a própria conta, aceitam os termos ou reduzem o acesso (sair,
revogar chave, excluir a conta). A sessão real passa por aqui, com cookie e CSRF: o bloqueio mora
na autenticação, que a sessão simulada dos outros testes substitui.
"""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import httpx2 as httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.identity import CURRENT_TERMS_VERSION, ExternalIdentity, resolve_login
from goblinshift.identity.tables import app_user, audit_event
from goblinshift.platform.db import unit_of_work
from tests.api.files_support import bearer
from tests.api.session_support import open_session, session_headers
from tests.api.support import assert_problem
from tests.identity.support import new_key, new_user
from tests.security.authz_matrix import Policy, entries

pytestmark = pytest.mark.integration

ME = "/api/v1/me"
ACCEPT = "/api/v1/me/terms:accept"
CATALOG = "/api/v1/catalog"
NEXT_VERSION = "v2-rascunho"
ACCOUNT_NAME = "Conta Google de teste"
# Rotas que a conta sem aceite continua usando: ler a própria conta, aceitar, sair, revogar
# chaves e excluir a conta. Todas as outras recusam.
EXEMPT_ROUTES = {
    ("GET", ME),
    ("POST", ACCEPT),
    ("DELETE", ME),
    ("POST", "/auth/logout-all"),
    ("GET", "/api/v1/api-keys"),
    ("DELETE", "/api/v1/api-keys/{key_id}"),
}


def external_account(factory: sessionmaker[Session]) -> UUID:
    identity = ExternalIdentity(
        issuer="https://accounts.google.com", subject=uuid4().hex, display_name=ACCOUNT_NAME
    )
    with unit_of_work(factory) as session:
        result = resolve_login(session, identity, signup_open=True, now=datetime.now(UTC))
    assert isinstance(result, UUID)
    return result


def signed_in(app: FastAPI, owner_id: UUID) -> dict[str, str]:
    return session_headers(app, open_session(app, owner_id))


def accept(
    client: TestClient, auth: dict[str, str], version: str = CURRENT_TERMS_VERSION
) -> httpx.Response:
    return client.post(ACCEPT, json={"version": version}, headers=auth)


def stored_terms(factory: sessionmaker[Session], owner_id: UUID) -> tuple[Any, Any]:
    with factory() as session:
        row = session.execute(
            select(app_user.c.terms_version, app_user.c.terms_accepted_at).where(
                app_user.c.id == owner_id
            )
        ).one()
    return row.terms_version, row.terms_accepted_at


def terms_events(factory: sessionmaker[Session], owner_id: UUID) -> list[tuple[str, str]]:
    with factory() as session:
        rows = session.execute(
            select(audit_event.c.event_type, audit_event.c.outcome)
            .where(audit_event.c.user_id == owner_id, audit_event.c.event_type == "TERMS_ACCEPTED")
            .order_by(audit_event.c.id)
        ).all()
    return [(row.event_type, row.outcome) for row in rows]


# --- Bloqueio até o aceite --------------------------------------------------------------------


def test_conta_sem_aceite_recebe_403_terms_not_accepted(
    app: FastAPI, client: TestClient, factory: sessionmaker[Session]
) -> None:
    owner = external_account(factory)

    response = client.get(CATALOG, headers=signed_in(app, owner))

    body = assert_problem(response, 403, "TERMS_NOT_ACCEPTED")
    assert CURRENT_TERMS_VERSION in body["detail"]
    assert str(owner) not in response.text
    assert ACCOUNT_NAME not in response.text


def test_chave_de_conta_sem_aceite_tambem_e_recusada(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    owner = external_account(factory)

    response = client.get(CATALOG, headers=bearer(new_key(factory, owner).token))

    assert_problem(response, 403, "TERMS_NOT_ACCEPTED")


@pytest.mark.parametrize(
    ("method", "path"),
    [
        pytest.param(method, path, id=f"{method} {path}")
        for method, path, _ in entries(
            Policy.ANY_CREDENTIAL, Policy.SCOPE, Policy.SESSION, Policy.RECENT_SESSION
        )
        if (method, path) not in EXEMPT_ROUTES
    ],
)
def test_toda_rota_autenticada_fora_da_lista_recusa_sem_aceite(
    app: FastAPI, client: TestClient, factory: sessionmaker[Session], method: str, path: str
) -> None:
    owner = external_account(factory)
    concrete = path.replace("{", "").replace("}", "")
    for name in ("upload_id", "artifact_id", "run_id", "key_id"):
        concrete = concrete.replace(name, str(uuid4()))

    response = client.request(method, concrete, headers=signed_in(app, owner), json={})

    assert_problem(response, 403, "TERMS_NOT_ACCEPTED")


def test_rotas_isentas_sao_as_esperadas() -> None:
    authenticated = {
        (method, path)
        for method, path, _ in entries(
            Policy.ANY_CREDENTIAL, Policy.SCOPE, Policy.SESSION, Policy.RECENT_SESSION
        )
    }

    assert authenticated >= EXEMPT_ROUTES


def test_get_me_sem_aceite_mostra_a_versao_a_aceitar(
    app: FastAPI, client: TestClient, factory: sessionmaker[Session]
) -> None:
    owner = external_account(factory)

    response = client.get(ME, headers=signed_in(app, owner))

    assert response.status_code == 200, response.text
    assert response.json()["terms"] == {
        "current_version": CURRENT_TERMS_VERSION,
        "accepted_version": None,
        "accepted_at": None,
        "acceptance_required": True,
    }


def test_conta_do_operador_nao_passa_pelo_aceite(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    operator = new_user(factory)
    auth = bearer(new_key(factory, operator).token)

    assert client.get(CATALOG, headers=auth).status_code == 200
    assert client.get(ME, headers=auth).json()["terms"]["acceptance_required"] is False


# --- Aceite -----------------------------------------------------------------------------------


def test_aceite_libera_as_rotas_e_e_auditado(
    app: FastAPI, client: TestClient, factory: sessionmaker[Session]
) -> None:
    owner = external_account(factory)
    auth = signed_in(app, owner)

    response = accept(client, auth)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body.keys() == {"version", "accepted_at"}
    assert body["version"] == CURRENT_TERMS_VERSION
    version, accepted_at = stored_terms(factory, owner)
    assert version == CURRENT_TERMS_VERSION
    assert datetime.fromisoformat(body["accepted_at"]) == accepted_at
    assert terms_events(factory, owner) == [("TERMS_ACCEPTED", "success")]
    assert client.get(CATALOG, headers=auth).status_code == 200
    me = client.get(ME, headers=auth).json()["terms"]
    assert me["accepted_version"] == CURRENT_TERMS_VERSION
    assert me["acceptance_required"] is False


def test_aceite_e_idempotente(
    app: FastAPI, client: TestClient, factory: sessionmaker[Session]
) -> None:
    owner = external_account(factory)
    auth = signed_in(app, owner)
    first = accept(client, auth)

    second = accept(client, auth)

    assert second.status_code == 200, second.text
    assert second.json() == first.json()
    assert terms_events(factory, owner) == [("TERMS_ACCEPTED", "success")]


def test_versao_nova_exige_novo_aceite(
    app: FastAPI, client: TestClient, factory: sessionmaker[Session]
) -> None:
    owner = external_account(factory)
    auth = signed_in(app, owner)
    accept(client, auth)
    app.state.terms_version = NEXT_VERSION

    blocked = client.get(CATALOG, headers=auth)
    reaccepted = accept(client, auth, NEXT_VERSION)

    assert_problem(blocked, 403, "TERMS_NOT_ACCEPTED")
    assert reaccepted.status_code == 200, reaccepted.text
    assert stored_terms(factory, owner)[0] == NEXT_VERSION
    assert client.get(CATALOG, headers=auth).status_code == 200
    assert len(terms_events(factory, owner)) == 2


def test_aceite_de_versao_que_nao_e_a_vigente_409_e_nada_gravado(
    app: FastAPI, client: TestClient, factory: sessionmaker[Session]
) -> None:
    owner = external_account(factory)
    auth = signed_in(app, owner)
    app.state.terms_version = NEXT_VERSION

    response = accept(client, auth, CURRENT_TERMS_VERSION)

    body = assert_problem(response, 409, "STATE_CONFLICT")
    assert NEXT_VERSION in body["detail"]
    assert stored_terms(factory, owner) == (None, None)
    assert terms_events(factory, owner) == []


def test_aceite_pela_chave_403_e_nada_gravado(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    owner = external_account(factory)

    response = accept(client, bearer(new_key(factory, owner).token))

    assert_problem(response, 403, "FORBIDDEN")
    assert stored_terms(factory, owner) == (None, None)


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"version": ""},
        {"version": "v" * 65},
        {"version": "v1\u0000"},
        {"version": 1},
    ],
    ids=["sem_versao", "vazia", "longa", "nul", "numero"],
)
def test_corpo_invalido_4xx_e_nada_gravado(
    app: FastAPI, client: TestClient, factory: sessionmaker[Session], payload: dict[str, Any]
) -> None:
    owner = external_account(factory)

    response = client.post(ACCEPT, json=payload, headers=signed_in(app, owner))

    assert response.status_code in {400, 422}, response.text
    assert stored_terms(factory, owner) == (None, None)


# --- Um usuário não aceita pelo outro ---------------------------------------------------------


@pytest.mark.parametrize("field", ["user_id", "owner_id", "id", "accepted_at"])
def test_campo_extra_422_e_nenhuma_conta_alterada(
    app: FastAPI, client: TestClient, factory: sessionmaker[Session], field: str
) -> None:
    victim = external_account(factory)
    attacker = external_account(factory)
    value = "2020-01-01T00:00:00Z" if field == "accepted_at" else str(victim)

    response = client.post(
        ACCEPT,
        json={"version": CURRENT_TERMS_VERSION, field: value},
        headers=signed_in(app, attacker),
    )

    assert_problem(response, 422, "VALIDATION_FAILED")
    assert stored_terms(factory, victim) == (None, None)
    assert stored_terms(factory, attacker) == (None, None)


def test_aceite_de_uma_conta_nao_libera_a_outra(
    app: FastAPI, client: TestClient, factory: sessionmaker[Session]
) -> None:
    first = external_account(factory)
    second = external_account(factory)
    first_auth = signed_in(app, first)
    second_auth = signed_in(app, second)

    accept(client, first_auth)

    assert client.get(CATALOG, headers=first_auth).status_code == 200
    assert_problem(client.get(CATALOG, headers=second_auth), 403, "TERMS_NOT_ACCEPTED")
    assert stored_terms(factory, second) == (None, None)
    assert terms_events(factory, second) == []


def test_aceite_sem_csrf_403_e_nada_gravado(
    app: FastAPI, client: TestClient, factory: sessionmaker[Session]
) -> None:
    owner = external_account(factory)
    issued = open_session(app, owner)

    response = client.post(
        ACCEPT,
        json={"version": CURRENT_TERMS_VERSION},
        headers=session_headers(app, issued, csrf_header="forjado"),
    )

    assert_problem(response, 403, "CSRF_FAILED")
    assert stored_terms(factory, owner) == (None, None)
