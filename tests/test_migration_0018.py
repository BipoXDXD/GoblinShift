"""Migração 0018: `app_user.awaiting_approval`, a conta pendente explícita (ADR 023).

Expand: coluna `NOT NULL DEFAULT false`. O backfill marca só a pendente pelo critério antigo
(bloqueada e sem nenhuma `web_session`), tirando a que tem `ACCOUNT_BLOCKED` na auditoria: a
bloqueada por abuso cujas sessões a retenção já apagou. O downgrade remove a coluna.
"""

from collections.abc import Iterator
from uuid import UUID

import pytest
from sqlalchemy import Connection, Engine, inspect, text
from sqlalchemy.engine.interfaces import ReflectedColumn

from goblinshift.platform.db import make_engine
from goblinshift.platform.settings import Settings
from tests.conftest import downgrade_migrations, run_migrations

pytestmark = pytest.mark.integration


@pytest.fixture
def engine(db_settings: Settings) -> Iterator[Engine]:
    engine = make_engine(db_settings)
    yield engine
    engine.dispose()
    run_migrations(db_settings, "head")


def at_0017(settings: Settings) -> None:
    """Leva o schema a 0017 venha ele de baixo (banco novo) ou de cima (head)."""
    run_migrations(settings, "0017")
    downgrade_migrations(settings, "0017")


def awaiting_approval(engine: Engine) -> ReflectedColumn | None:
    columns = inspect(engine).get_columns("app_user")
    return next((column for column in columns if column["name"] == "awaiting_approval"), None)


def new_user(conn: Connection, status: str) -> UUID:
    user_id: UUID = conn.execute(
        text(
            "INSERT INTO app_user (issuer, subject, display_name, status) "
            "VALUES ('teste', gen_random_uuid()::text, 'Ana', :status) RETURNING id"
        ),
        {"status": status},
    ).scalar_one()
    return user_id


def new_session(conn: Connection, user_id: UUID) -> None:
    conn.execute(
        text(
            "INSERT INTO web_session (user_id, secret_hash, authenticated_at, last_seen_at, "
            "idle_expires_at, absolute_expires_at) "
            "VALUES (:user_id, sha256(gen_random_uuid()::text::bytea), now(), now(), "
            "now() + interval '1 hour', now() + interval '8 hours')"
        ),
        {"user_id": user_id},
    )


def record_block(conn: Connection, user_id: UUID) -> None:
    conn.execute(
        text(
            "INSERT INTO audit_event (user_id, event_type, outcome) "
            "VALUES (:user_id, 'ACCOUNT_BLOCKED', 'success')"
        ),
        {"user_id": user_id},
    )


def pending_flags(engine: Engine, *user_ids: UUID) -> dict[UUID, bool]:
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT id, awaiting_approval FROM app_user WHERE id = ANY(:ids)"),
            {"ids": list(user_ids)},
        ).all()
    return {row.id: row.awaiting_approval for row in rows}


def test_0018_cria_a_marca_obrigatoria_com_default_false(
    db_settings: Settings, engine: Engine
) -> None:
    run_migrations(db_settings, "0018")

    column = awaiting_approval(engine)

    assert column is not None
    assert column["nullable"] is False
    assert column["default"] == "false"


def test_backfill_marca_so_a_pendente_legitima(db_settings: Settings, engine: Engine) -> None:
    at_0017(db_settings)
    with engine.begin() as conn:
        pending = new_user(conn, "blocked")
        blocked_without_sessions = new_user(conn, "blocked")
        record_block(conn, blocked_without_sessions)
        active = new_user(conn, "active")
        new_session(conn, active)

    run_migrations(db_settings, "0018")

    assert pending_flags(engine, pending, blocked_without_sessions, active) == {
        pending: True,
        blocked_without_sessions: False,
        active: False,
    }


def test_downgrade_da_0018_remove_a_marca(db_settings: Settings, engine: Engine) -> None:
    run_migrations(db_settings, "0018")

    downgrade_migrations(db_settings, "0017")

    assert awaiting_approval(engine) is None
