"""Autenticação por chave de API e escopos na API (docs/GoblinShiftPlan.md, 1.6 e 3.6)."""

import logging
import secrets
from datetime import UTC, datetime, timedelta

import httpx2 as httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.entrypoints.api.dependencies import current_time
from goblinshift.identity import Scope, revoke_key
from goblinshift.platform.db import unit_of_work
from tests.api.support import assert_problem
from tests.identity.support import new_key, new_user

pytestmark = pytest.mark.integration

ME = "/api/v1/me"
FILES_WRITE = "/api/v1/_test/files-write"
NOW = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def freeze_clock(app: FastAPI, now: datetime) -> None:
    app.dependency_overrides[current_time] = lambda: now


def assert_refused_without_account_data(response: httpx.Response) -> None:
    assert_problem(response, 401, "AUTHENTICATION_REQUIRED")
    assert response.headers["www-authenticate"] == "Bearer"
    assert "Conta de teste" not in response.text


def test_me_devolve_a_conta_e_os_escopos_da_chave(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    user_id = new_user(factory)
    scopes = frozenset({Scope.RUNS_WRITE, Scope.FILES_READ})
    issued = new_key(factory, user_id, scopes=scopes)

    response = client.get(ME, headers=bearer(issued.token))

    assert response.status_code == 200
    assert response.json() == {
        "id": str(user_id),
        "display_name": "Conta de teste",
        "scopes": ["files:read", "runs:write"],
    }


def test_esquema_bearer_aceita_maiusculas_e_minusculas(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    issued = new_key(factory, new_user(factory))

    response = client.get(ME, headers={"Authorization": f"bEaReR {issued.token}"})

    assert response.status_code == 200


@pytest.mark.parametrize(
    "headers",
    [
        pytest.param({}, id="sem-header"),
        pytest.param({"Authorization": "Bearer"}, id="bearer-sem-chave"),
        pytest.param({"Authorization": "Bearer   "}, id="bearer-em-branco"),
        pytest.param({"Authorization": "Basic dXNlcjpwYXNz"}, id="basic"),
        pytest.param({"Authorization": "Bearer nao-e-uma-chave"}, id="malformada"),
        pytest.param(
            {"Authorization": "Bearer gsk_" + "0" * 16 + "_" + "A" * 43}, id="desconhecida"
        ),
    ],
)
def test_credencial_ausente_ou_invalida_responde_401(
    client: TestClient, factory: sessionmaker[Session], headers: dict[str, str]
) -> None:
    new_key(factory, new_user(factory))

    response = client.get(ME, headers=headers)

    assert_refused_without_account_data(response)


def test_dois_headers_authorization_respondem_401_mesmo_com_chave_valida(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    issued = new_key(factory, new_user(factory))
    auth = f"Bearer {issued.token}"

    response = client.get(ME, headers=[("Authorization", auth), ("Authorization", auth)])

    assert_problem(response, 401, "AUTHENTICATION_REQUIRED")


def test_chave_expirada_exatamente_agora_responde_401(
    app: FastAPI, client: TestClient, factory: sessionmaker[Session]
) -> None:
    issued = new_key(factory, new_user(factory), now=NOW, days=1)
    expires_at = NOW + timedelta(days=1)

    freeze_clock(app, expires_at - timedelta(microseconds=1))
    assert client.get(ME, headers=bearer(issued.token)).status_code == 200

    freeze_clock(app, expires_at)
    response = client.get(ME, headers=bearer(issued.token))
    assert_refused_without_account_data(response)


def test_chave_revogada_responde_401(client: TestClient, factory: sessionmaker[Session]) -> None:
    issued = new_key(factory, new_user(factory))
    assert client.get(ME, headers=bearer(issued.token)).status_code == 200

    with unit_of_work(factory) as session:
        revoke_key(session, issued.public_id, datetime.now(UTC))
    response = client.get(ME, headers=bearer(issued.token))

    assert_refused_without_account_data(response)


def test_conta_bloqueada_responde_401(client: TestClient, factory: sessionmaker[Session]) -> None:
    issued = new_key(factory, new_user(factory, blocked=True))

    response = client.get(ME, headers=bearer(issued.token))

    assert_refused_without_account_data(response)


def test_escopo_insuficiente_responde_403(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    scopes = frozenset({Scope.FILES_READ, Scope.RUNS_WRITE})
    issued = new_key(factory, new_user(factory), scopes=scopes)

    response = client.get(FILES_WRITE, headers=bearer(issued.token))

    body = assert_problem(response, 403, "INSUFFICIENT_SCOPE")
    assert "files:write" in body["detail"]


def test_escopo_exigido_presente_libera_a_rota(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    user_id = new_user(factory)
    issued = new_key(factory, user_id, scopes=frozenset({Scope.FILES_WRITE}))

    response = client.get(FILES_WRITE, headers=bearer(issued.token))

    assert response.status_code == 200
    assert response.json() == {"owner_id": str(user_id)}


def test_escopo_sem_credencial_responde_401_e_nao_403(client: TestClient) -> None:
    response = client.get(FILES_WRITE)

    assert_problem(response, 401, "AUTHENTICATION_REQUIRED")


def test_segredo_da_chave_nunca_aparece_nos_logs(
    client: TestClient, factory: sessionmaker[Session], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    valid = new_key(factory, new_user(factory))
    revoked = new_key(factory, new_user(factory))
    with unit_of_work(factory) as session:
        revoke_key(session, revoked.public_id, datetime.now(UTC))
    # Canário com o formato de uma chave real, gerado na hora para não ir parar no git.
    canary = f"gsk_{secrets.token_hex(8)}_CANARY{secrets.token_urlsafe(32)[:37]}"

    responses = [
        client.get(ME, headers=bearer(valid.token)),
        client.get(ME, headers=bearer(revoked.token)),
        client.get(ME, headers=bearer(canary)),
        client.get(FILES_WRITE, headers=bearer(canary)),
    ]

    assert [response.status_code for response in responses] == [200, 401, 401, 401]
    # Controle positivo: a recusa da chave revogada foi registrada, só pelo identificador.
    assert revoked.public_id in caplog.text
    captured = caplog.text + "".join(repr(record.args) for record in caplog.records)
    for token in (valid.token, revoked.token, canary):
        # Tamanho fixo: o base64url do segredo também pode conter "_".
        secret = token[-43:]
        assert secret not in captured
        assert all(secret not in response.text for response in responses)
