"""`GET`, `POST /api-keys` e `DELETE /api-keys/{key_id}` pela sessão (GoblinShiftPlan.md, 3.6)."""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid7

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.identity import MAX_ACTIVE_KEYS, authenticate_api_key
from goblinshift.identity.tables import api_key, app_user, audit_event
from goblinshift.platform.db import unit_of_work
from tests.api.simulated_session import SimulatedSessions
from tests.api.support import assert_problem
from tests.identity.support import new_user

pytestmark = pytest.mark.integration

KEYS = "/api/v1/api-keys"
NOW = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)
VALID = {"scopes": ["files:read", "runs:write"], "days": 30}
SUMMARY_KEYS = {
    "id",
    "public_id",
    "scopes",
    "created_at",
    "expires_at",
    "last_used_at",
    "revoked_at",
}


@pytest.fixture
def sessions(app: FastAPI) -> SimulatedSessions:
    return SimulatedSessions(app, NOW)


def create_key(client: TestClient, body: object = VALID) -> dict[str, Any]:
    response = client.post(KEYS, json=body)
    assert response.status_code == 201, response.text
    created: dict[str, Any] = response.json()
    return created


def key_count(factory: sessionmaker[Session], owner_id: UUID) -> int:
    with factory() as session:
        count: int = session.execute(
            select(func.count()).select_from(api_key).where(api_key.c.owner_id == owner_id)
        ).scalar_one()
    return count


def audit_events(factory: sessionmaker[Session], owner_id: UUID) -> list[tuple[str, str]]:
    with factory() as session:
        rows = session.execute(
            select(audit_event.c.event_type, audit_event.c.outcome)
            .where(audit_event.c.user_id == owner_id)
            .order_by(audit_event.c.id)
        ).all()
    return [(row.event_type, row.outcome) for row in rows]


def authenticates(factory: sessionmaker[Session], token: str) -> bool:
    with unit_of_work(factory) as session:
        return authenticate_api_key(session, token, NOW) is not None


# --- Emissão ----------------------------------------------------------------------------------


def test_post_cria_chave_e_mostra_o_segredo_uma_vez(
    client: TestClient, factory: sessionmaker[Session], sessions: SimulatedSessions
) -> None:
    owner = new_user(factory)
    sessions.sign_in(owner)

    response = client.post(KEYS, json=VALID)

    assert response.status_code == 201, response.text
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert set(body) == {"id", "public_id", "token", "scopes", "expires_at"}
    assert sorted(body["scopes"]) == ["files:read", "runs:write"]
    assert datetime.fromisoformat(body["expires_at"]) == NOW + timedelta(days=30)
    assert body["token"].startswith(f"gsk_{body['public_id']}_")
    assert authenticates(factory, body["token"])
    assert audit_events(factory, owner) == [("KEY_ISSUED", "success")]


def test_get_depois_do_post_nao_traz_segredo_nem_hash(
    client: TestClient, factory: sessionmaker[Session], sessions: SimulatedSessions
) -> None:
    sessions.sign_in(new_user(factory))
    created = create_key(client)

    response = client.get(KEYS)

    assert response.status_code == 200, response.text
    [listed] = response.json()["data"]
    assert set(response.json()) == {"data"}
    assert set(listed) == SUMMARY_KEYS
    assert listed["id"] == created["id"]
    assert listed["public_id"] == created["public_id"]
    secret = created["token"].rsplit("_", 1)[1]
    assert secret not in response.text
    assert "hash" not in response.text


@pytest.mark.parametrize(
    "extra",
    [
        {"owner_id": "00000000-0000-0000-0000-000000000001"},
        {"public_id": "0123456789abcdef"},
        {"expires_at": "2099-01-01T00:00:00Z"},
        {"token": "gsk_x"},
    ],
    ids=["owner_id", "public_id", "expires_at", "token"],
)
def test_post_com_campo_extra_422_e_nada_gravado(
    client: TestClient,
    factory: sessionmaker[Session],
    sessions: SimulatedSessions,
    extra: dict[str, str],
) -> None:
    owner = new_user(factory)
    sessions.sign_in(owner)

    response = client.post(KEYS, json={**VALID, **extra})

    assert_problem(response, 422, "VALIDATION_FAILED")
    assert key_count(factory, owner) == 0


@pytest.mark.parametrize(
    "body",
    [
        {"scopes": ["admin"], "days": 30},
        {"scopes": ["files:read", "files:*"], "days": 30},
        {"scopes": [], "days": 30},
        {"scopes": "files:read", "days": 30},
        {"days": 30},
        {"scopes": ["files:read"], "days": 0},
        {"scopes": ["files:read"], "days": 91},
        {"scopes": ["files:read"], "days": "30"},
        {"scopes": ["files:read"], "days": 30.5},
        {"scopes": ["files:read\u0000"], "days": 30},
    ],
    ids=[
        "escopo-desconhecido",
        "curinga",
        "sem-escopo",
        "escopo-texto",
        "sem-escopos",
        "0-dias",
        "91-dias",
        "dias-texto",
        "dias-fracao",
        "nul",
    ],
)
def test_post_invalido_422_e_nada_gravado(
    client: TestClient, factory: sessionmaker[Session], sessions: SimulatedSessions, body: object
) -> None:
    owner = new_user(factory)
    sessions.sign_in(owner)

    response = client.post(KEYS, json=body)

    assert_problem(response, 422, "VALIDATION_FAILED")
    assert key_count(factory, owner) == 0


@pytest.mark.parametrize("days", [1, 90])
def test_post_aceita_as_fronteiras_de_validade(
    client: TestClient, factory: sessionmaker[Session], sessions: SimulatedSessions, days: int
) -> None:
    sessions.sign_in(new_user(factory))

    created = create_key(client, {"scopes": ["files:read"], "days": days})

    assert datetime.fromisoformat(created["expires_at"]) == NOW + timedelta(days=days)


def test_post_sem_dias_usa_30(
    client: TestClient, factory: sessionmaker[Session], sessions: SimulatedSessions
) -> None:
    sessions.sign_in(new_user(factory))

    created = create_key(client, {"scopes": ["files:read"]})

    assert datetime.fromisoformat(created["expires_at"]) == NOW + timedelta(days=30)


def test_sexta_chave_ativa_409(
    client: TestClient, factory: sessionmaker[Session], sessions: SimulatedSessions
) -> None:
    owner = new_user(factory)
    sessions.sign_in(owner)
    for _ in range(MAX_ACTIVE_KEYS):
        create_key(client)

    response = client.post(KEYS, json=VALID)

    assert_problem(response, 409, "STATE_CONFLICT")
    assert key_count(factory, owner) == MAX_ACTIVE_KEYS


def test_emitir_chave_com_login_de_11_min_401_reauth_required(
    client: TestClient, factory: sessionmaker[Session], sessions: SimulatedSessions
) -> None:
    owner = new_user(factory)
    sessions.sign_in(owner, minutes_ago=11)

    response = client.post(KEYS, json=VALID)

    assert_problem(response, 401, "REAUTH_REQUIRED")
    assert key_count(factory, owner) == 0


def test_emitir_chave_com_login_de_10_min_exatos_aceita(
    client: TestClient, factory: sessionmaker[Session], sessions: SimulatedSessions
) -> None:
    sessions.sign_in(new_user(factory), minutes_ago=10)

    create_key(client)


def test_conta_bloqueada_depois_da_autenticacao_nao_emite(
    client: TestClient, factory: sessionmaker[Session], sessions: SimulatedSessions
) -> None:
    # O bloqueio pode chegar entre a autenticação e a emissão: a trava da conta decide.
    owner = new_user(factory)
    sessions.sign_in(owner)
    with unit_of_work(factory) as session:
        session.execute(update(app_user).where(app_user.c.id == owner).values(status="blocked"))

    response = client.post(KEYS, json=VALID)

    assert_problem(response, 401, "AUTHENTICATION_REQUIRED")
    assert key_count(factory, owner) == 0


# --- Listagem ---------------------------------------------------------------------------------


def test_get_lista_so_as_chaves_da_propria_conta(
    client: TestClient, factory: sessionmaker[Session], sessions: SimulatedSessions
) -> None:
    owner, intruder = new_user(factory), new_user(factory)
    sessions.sign_in(owner)
    owned = create_key(client)
    sessions.sign_in(intruder)

    response = client.get(KEYS)

    assert response.status_code == 200, response.text
    assert response.json() == {"data": []}
    assert owned["public_id"] not in response.text
    assert str(owner) not in response.text


# --- Revogação --------------------------------------------------------------------------------


def test_delete_revoga_a_propria_chave_e_repetir_e_204(
    client: TestClient, factory: sessionmaker[Session], sessions: SimulatedSessions
) -> None:
    owner = new_user(factory)
    sessions.sign_in(owner)
    created = create_key(client)

    first = client.delete(f"{KEYS}/{created['id']}")
    second = client.delete(f"{KEYS}/{created['id']}")

    assert first.status_code == 204
    assert first.content == b""
    assert second.status_code == 204
    assert not authenticates(factory, created["token"])
    [listed] = client.get(KEYS).json()["data"]
    assert datetime.fromisoformat(listed["revoked_at"]) == NOW
    assert audit_events(factory, owner) == [
        ("KEY_ISSUED", "success"),
        ("KEY_REVOKED", "success"),
        ("KEY_REVOKED", "success"),
    ]


def test_outra_conta_recebe_404_sem_dados_e_a_chave_fica_intacta(
    client: TestClient, factory: sessionmaker[Session], sessions: SimulatedSessions
) -> None:
    owner, intruder = new_user(factory), new_user(factory)
    sessions.sign_in(owner)
    created = create_key(client)
    sessions.sign_in(intruder)

    response = client.delete(f"{KEYS}/{created['id']}")

    body = assert_problem(response, 404, "NOT_FOUND")
    # `instance` só ecoa o caminho que o próprio intruso enviou; o resto não traz nada da dona.
    assert body.pop("instance") == f"{KEYS}/{created['id']}"
    for owner_data in (created["id"], created["public_id"], str(owner)):
        assert owner_data not in str(body)
    assert authenticates(factory, created["token"])
    with factory() as session:
        revoked_at = session.execute(
            select(api_key.c.revoked_at).where(api_key.c.public_id == created["public_id"])
        ).scalar_one()
    assert revoked_at is None
    assert audit_events(factory, intruder) == []


def test_delete_de_chave_inexistente_404(
    client: TestClient, factory: sessionmaker[Session], sessions: SimulatedSessions
) -> None:
    sessions.sign_in(new_user(factory))

    response = client.delete(f"{KEYS}/{uuid7()}")

    assert_problem(response, 404, "NOT_FOUND")


def test_delete_com_id_que_nao_e_uuid_422(
    client: TestClient, factory: sessionmaker[Session], sessions: SimulatedSessions
) -> None:
    sessions.sign_in(new_user(factory))

    response = client.delete(f"{KEYS}/nao-e-uuid")

    assert_problem(response, 422, "VALIDATION_FAILED")
