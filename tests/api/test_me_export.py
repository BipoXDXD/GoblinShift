"""`GET /api/v1/me/export`: os dados da própria conta em JSON (#442, 32c e 52d; LGPD, art. 19).

Só a sessão com login recente exporta; o corpo traz só a conta da credencial e nunca segredo:
nem hash de chave ou de sessão, nem o id da sessão, nem o `subject` do provedor.
"""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.entrypoints.api.dependencies import current_time
from goblinshift.identity import AuditEventType, AuditOutcome, record_audit_event
from goblinshift.identity.tables import api_key, app_user, web_session
from goblinshift.platform.db import unit_of_work
from goblinshift.presets.drafts import parse_draft
from goblinshift.presets.store import create_preset
from tests.api.files_support import bearer
from tests.api.session_support import open_session, session_headers
from tests.api.support import assert_problem
from tests.identity.support import new_key, new_user
from tests.runs.worker_support import queue_run

pytestmark = pytest.mark.integration

EXPORT = "/api/v1/me/export"
NOW = datetime(2026, 11, 28, 12, 0, tzinfo=UTC)
A_IP = "203.0.113.77"
B_IP = "198.51.100.23"


@pytest.fixture(autouse=True)
def frozen_clock(app: FastAPI) -> None:
    app.dependency_overrides[current_time] = lambda: NOW


def signed_in(app: FastAPI, owner_id: UUID, *, minutes_ago: float = 1) -> dict[str, str]:
    issued = open_session(
        app, owner_id, authenticated_at=NOW - timedelta(minutes=minutes_ago), now=NOW
    )
    return session_headers(app, issued)


def audit(factory: sessionmaker[Session], owner_id: UUID, ip: str) -> None:
    with unit_of_work(factory) as session:
        record_audit_event(
            session,
            event=AuditEventType.LOGIN_SUCCEEDED,
            user_id=owner_id,
            ip=ip,
            outcome=AuditOutcome.SUCCESS,
            now=NOW - timedelta(hours=1),
        )


def preset(factory: sessionmaker[Session], owner_id: UUID, name: str) -> UUID:
    with unit_of_work(factory) as session:
        return create_preset(session, owner_id, parse_draft(name, "csv-janitor", 1, {})).id


def secrets_of(factory: sessionmaker[Session], owner_id: UUID) -> list[str]:
    """Tudo que identifica ou abre a conta e não pode sair na exportação."""
    with factory() as session:
        subject = session.execute(
            select(app_user.c.subject).where(app_user.c.id == owner_id)
        ).scalar_one()
        key_hashes = session.execute(
            select(api_key.c.secret_hash).where(api_key.c.owner_id == owner_id)
        ).scalars()
        sessions = session.execute(
            select(web_session.c.id, web_session.c.secret_hash).where(
                web_session.c.user_id == owner_id
            )
        ).all()
    values = [subject, *(digest.hex() for digest in key_hashes)]
    for row in sessions:
        values += [str(row.id), row.secret_hash.hex()]
    return values


def test_exportacao_traz_so_a_propria_conta(
    app: FastAPI, client: TestClient, factory: sessionmaker[Session]
) -> None:
    owner, other = new_user(factory), new_user(factory)
    owner_key = new_key(factory, owner, now=NOW)
    other_key = new_key(factory, other, now=NOW)
    owner_run = queue_run(factory, app.state.storage, owner)
    other_run = queue_run(factory, app.state.storage, other)
    preset(factory, owner, "Mensal da Ana")
    other_preset = preset(factory, other, "Semanal do Bruno")
    audit(factory, owner, A_IP)
    audit(factory, other, B_IP)
    signed_in(app, other)

    response = client.get(EXPORT, headers=signed_in(app, owner))

    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    assert body["account"]["id"] == str(owner)
    assert [key["public_id"] for key in body["api_keys"]] == [owner_key.public_id]
    assert [run["id"] for run in body["runs"]] == [str(owner_run)]
    assert [item["name"] for item in body["presets"]] == ["Mensal da Ana"]
    assert len(body["sessions"]) == 1
    assert all(event["event_type"] for event in body["security_events"])
    for foreign in (str(other), other_key.public_id, str(other_run), str(other_preset), "198.51"):
        assert foreign not in response.text


def test_exportacao_traz_limites_uso_termos_e_resumo_das_execucoes(
    app: FastAPI, client: TestClient, factory: sessionmaker[Session]
) -> None:
    owner = new_user(factory)
    run_id = queue_run(factory, app.state.storage, owner)

    body = client.get(EXPORT, headers=signed_in(app, owner)).json()

    assert body["account"]["status"] == "active"
    assert body["account"]["display_name"] == "Conta de teste"
    assert body["terms"] == {"accepted_version": None, "accepted_at": None}
    assert body["limits"]["active_runs"] == 1
    assert body["usage"]["runs_last_24h"] == 1
    assert body["usage"]["active_run_id"] == str(run_id)
    [summary] = body["runs"]
    assert summary["recipe_id"] == "csv-janitor"
    assert summary["status"] == "queued"
    assert (summary["processed"], summary["total"]) == (0, 1)
    assert isinstance(summary["parameters"], dict)
    assert body["generated_at"]


def test_exportacao_exige_login_recente_e_sessao(
    app: FastAPI, client: TestClient, factory: sessionmaker[Session]
) -> None:
    owner = new_user(factory)

    stale = client.get(EXPORT, headers=signed_in(app, owner, minutes_ago=11))
    by_key = client.get(EXPORT, headers=bearer(new_key(factory, owner, now=NOW).token))

    # Os mesmos códigos de `DELETE /me`.
    assert_problem(stale, 401, "REAUTH_REQUIRED")
    assert_problem(by_key, 403, "FORBIDDEN")


def test_exportacao_nao_traz_segredo_nem_subject(
    app: FastAPI, client: TestClient, factory: sessionmaker[Session]
) -> None:
    owner = new_user(factory)
    issued = new_key(factory, owner, now=NOW)
    headers = signed_in(app, owner)

    response = client.get(EXPORT, headers=headers)

    assert response.status_code == 200, response.text
    secrets = [*secrets_of(factory, owner), issued.token]
    assert len(secrets) >= 4
    for secret in secrets:
        assert secret not in response.text
    [key] = response.json()["api_keys"]
    assert set(key) == {
        "public_id",
        "scopes",
        "created_at",
        "expires_at",
        "last_used_at",
        "revoked_at",
    }
    [session] = response.json()["sessions"]
    assert set(session) == {
        "created_at",
        "authenticated_at",
        "last_seen_at",
        "idle_expires_at",
        "absolute_expires_at",
        "revoked_at",
    }
    assert "subject" not in response.json()["account"]


def test_exportacao_inclui_os_eventos_de_seguranca_com_ip_truncado(
    app: FastAPI, client: TestClient, factory: sessionmaker[Session]
) -> None:
    owner = new_user(factory)
    audit(factory, owner, A_IP)

    response = client.get(EXPORT, headers=signed_in(app, owner))

    [event] = [item for item in response.json()["security_events"] if item["ip_prefix"] is not None]
    assert event == {
        "event_type": "LOGIN_SUCCEEDED",
        "outcome": "success",
        "occurred_at": (NOW - timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
        "ip_prefix": "203.0.113.0/24",
        "details": None,
    }
    assert A_IP not in response.text


def test_exportacao_sai_como_anexo_sem_cache(
    app: FastAPI, client: TestClient, factory: sessionmaker[Session]
) -> None:
    owner = new_user(factory)

    response = client.get(EXPORT, headers=signed_in(app, owner))

    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/json"
    assert (
        response.headers["content-disposition"] == 'attachment; filename="goblinshift-conta.json"'
    )
    assert response.headers["cache-control"] == "no-store"


def test_exportacao_vale_sem_o_aceite_dos_termos_vigentes(
    app: FastAPI, client: TestClient, factory: sessionmaker[Session]
) -> None:
    """Exportar é ler a própria conta, como o `GET /me`: não depende de aceitar termos novos."""
    owner = new_user(factory)
    with unit_of_work(factory) as session:
        session.execute(update(app_user).where(app_user.c.id == owner).values(issuer="google"))

    response = client.get(EXPORT, headers=signed_in(app, owner))

    assert response.status_code == 200, response.text
