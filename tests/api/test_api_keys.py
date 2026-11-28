"""`GET`, `POST /api-keys` e `DELETE /api-keys/{key_id}` pela sessão (GoblinShiftPlan.md, 3.6)."""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid7

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.entrypoints.api import api_keys as api_keys_route
from goblinshift.identity import (
    MAX_ACTIVE_KEYS,
    MAX_KEY_DAYS,
    MAX_STORED_KEYS,
    Scope,
    authenticate_api_key,
    issue_key,
    issue_key_for_session,
    revoke_session,
)
from goblinshift.identity.keys import MIN_KEY_DAYS
from goblinshift.identity.tables import api_key, app_user, audit_event
from goblinshift.platform.db import unit_of_work
from tests.api.simulated_session import SimulatedSessions
from tests.api.support import assert_problem
from tests.identity.support import HASHER, new_user

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
    return SimulatedSessions(app, NOW, persisted=True)


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
        return authenticate_api_key(session, token, NOW, hasher=HASHER) is not None


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


@pytest.mark.parametrize("days", [MIN_KEY_DAYS, MAX_KEY_DAYS], ids=["minimo", "maximo"])
def test_post_aceita_as_bordas_da_validade(
    client: TestClient, factory: sessionmaker[Session], sessions: SimulatedSessions, days: int
) -> None:
    owner = new_user(factory)
    sessions.sign_in(owner)

    response = client.post(KEYS, json={**VALID, "days": days})

    assert response.status_code == 201, response.text
    assert datetime.fromisoformat(response.json()["expires_at"]) == NOW + timedelta(days=days)
    assert key_count(factory, owner) == 1


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
    # gsk_<public_id>_<secret>: o segredo pode conter "_", então divide só nos dois primeiros.
    secret = created["token"].split("_", 2)[2]
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


def test_post_com_nul_400_json_invalid_e_nada_gravado(
    client: TestClient, factory: sessionmaker[Session], sessions: SimulatedSessions
) -> None:
    owner = new_user(factory)
    sessions.sign_in(owner)

    response = client.post(KEYS, json={"scopes": ["files:read\u0000"], "days": 30})

    assert_problem(response, 400, "JSON_INVALID")
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


def test_centesima_primeira_chave_guardada_409_com_mensagem_propria(
    client: TestClient, factory: sessionmaker[Session], sessions: SimulatedSessions
) -> None:
    owner = new_user(factory)
    sessions.sign_in(owner)
    with unit_of_work(factory) as session:
        for _ in range(MAX_STORED_KEYS):
            issue_key(session, owner_id=owner, scopes=frozenset({Scope.FILES_READ}), now=NOW)
        session.execute(update(api_key).where(api_key.c.owner_id == owner).values(revoked_at=NOW))

    response = client.post(KEYS, json=VALID)

    body = assert_problem(response, 409, "STATE_CONFLICT")
    # Revogar não abre vaga: a mensagem diz que as inativas saem com o tempo.
    assert f"{MAX_STORED_KEYS} chaves guardadas" in body["detail"]
    assert key_count(factory, owner) == MAX_STORED_KEYS


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


def test_post_api_keys_com_sessao_revogada_no_meio_401(
    client: TestClient,
    factory: sessionmaker[Session],
    sessions: SimulatedSessions,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # O `logout` (ou o `logout-all`) confirma entre a autenticação e a emissão: o Principal já foi
    # lido, então só a reconferência da sessão sob a trava da conta impede a chave.
    owner = new_user(factory)
    sessions.sign_in(owner)

    def revoke_then_issue(session: Session, **kwargs: Any) -> object:
        with unit_of_work(factory) as other:
            revoke_session(other, sessions.session_id, NOW)
        return issue_key_for_session(session, **kwargs)

    monkeypatch.setattr(api_keys_route, "issue_key_for_session", revoke_then_issue)

    response = client.post(KEYS, json=VALID)

    assert_problem(response, 401, "AUTHENTICATION_REQUIRED")
    assert key_count(factory, owner) == 0


# --- Idempotency-Key -------------------------------------------------------------------------

IDEM = "repeticao-do-cliente-0001"


def post_with_key(client: TestClient, idem_key: str, body: object = VALID) -> Any:
    return client.post(KEYS, json=body, headers={"Idempotency-Key": idem_key})


def test_repeticao_com_a_mesma_chave_409_com_o_id_e_sem_segunda_linha(
    client: TestClient, factory: sessionmaker[Session], sessions: SimulatedSessions
) -> None:
    # O cliente repete o POST depois de um timeout: a primeira chave existe, o segredo se perdeu.
    owner = new_user(factory)
    sessions.sign_in(owner)
    first = post_with_key(client, IDEM)
    assert first.status_code == 201, first.text

    repeated = post_with_key(client, IDEM)

    body = assert_problem(repeated, 409, "STATE_CONFLICT")
    created = first.json()
    assert created["public_id"] in body["detail"]
    assert created["id"] in body["detail"]
    assert created["token"].split("_", 2)[2] not in repeated.text
    assert key_count(factory, owner) == 1
    assert audit_events(factory, owner) == [("KEY_ISSUED", "success")]


def test_repeticao_depois_da_revogacao_continua_409(
    client: TestClient, factory: sessionmaker[Session], sessions: SimulatedSessions
) -> None:
    owner = new_user(factory)
    sessions.sign_in(owner)
    created = post_with_key(client, IDEM).json()
    assert client.delete(f"{KEYS}/{created['id']}").status_code == 204

    repeated = post_with_key(client, IDEM)

    body = assert_problem(repeated, 409, "STATE_CONFLICT")
    assert created["id"] in body["detail"]
    assert key_count(factory, owner) == 1


@pytest.mark.parametrize("full", ["ativas", "guardadas"])
def test_repeticao_com_o_teto_cheio_responde_a_repeticao(
    client: TestClient, factory: sessionmaker[Session], sessions: SimulatedSessions, full: str
) -> None:
    # A busca pela Idempotency-Key vem antes dos tetos: a repetição não vira o 409 de teto.
    owner = new_user(factory)
    sessions.sign_in(owner)
    with unit_of_work(factory) as session:
        if full == "ativas":
            for _ in range(MAX_ACTIVE_KEYS - 1):
                issue_key(session, owner_id=owner, scopes=frozenset({Scope.FILES_READ}), now=NOW)
        else:
            for _ in range(MAX_STORED_KEYS - 1):
                issue_key(session, owner_id=owner, scopes=frozenset({Scope.FILES_READ}), now=NOW)
            session.execute(
                update(api_key).where(api_key.c.owner_id == owner).values(revoked_at=NOW)
            )
    created = post_with_key(client, IDEM).json()

    repeated = post_with_key(client, IDEM)

    body = assert_problem(repeated, 409, "STATE_CONFLICT")
    assert created["public_id"] in body["detail"]


def test_mesma_chave_em_outra_conta_emite(
    client: TestClient, factory: sessionmaker[Session], sessions: SimulatedSessions
) -> None:
    owner, other = new_user(factory), new_user(factory)
    sessions.sign_in(owner)
    assert post_with_key(client, IDEM).status_code == 201
    sessions.sign_in(other)

    response = post_with_key(client, IDEM)

    assert response.status_code == 201, response.text
    assert key_count(factory, other) == 1


def test_sem_idempotency_key_duas_emissoes(
    client: TestClient, factory: sessionmaker[Session], sessions: SimulatedSessions
) -> None:
    owner = new_user(factory)
    sessions.sign_in(owner)

    first, second = create_key(client), create_key(client)

    assert first["id"] != second["id"]
    assert key_count(factory, owner) == 2


@pytest.mark.parametrize(
    "idem_key", ["a" * 15, "a" * 65, "a" * 16 + "\x00"], ids=["15", "65", "nul"]
)
def test_idempotency_key_malformada_400(
    client: TestClient, factory: sessionmaker[Session], sessions: SimulatedSessions, idem_key: str
) -> None:
    owner = new_user(factory)
    sessions.sign_in(owner)

    response = post_with_key(client, idem_key)

    assert_problem(response, 400, "IDEMPOTENCY_KEY_INVALID")
    assert key_count(factory, owner) == 0


@pytest.mark.parametrize("idem_key", ["a" * 16, "Z9_-" * 16], ids=["16", "64"])
def test_idempotency_key_nas_bordas_emite(
    client: TestClient, factory: sessionmaker[Session], sessions: SimulatedSessions, idem_key: str
) -> None:
    owner = new_user(factory)
    sessions.sign_in(owner)

    response = post_with_key(client, idem_key)

    assert response.status_code == 201, response.text
    assert key_count(factory, owner) == 1


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
