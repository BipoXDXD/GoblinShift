"""Migração 0019: índices das buscas da manutenção e da FK de `object_use` (#442).

Expand: só índices novos, que o código anterior ignora. O downgrade os remove. A 0019 também
normaliza a marca de pendente que a API antiga deixou errada na janela do deploy da 0018.
"""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import Connection, Engine, event, text
from sqlalchemy.orm import Session

from goblinshift.identity.accounts import block_account, list_pending_accounts
from goblinshift.identity.purge import (
    AUDIT_RETENTION,
    delete_ended_web_sessions,
    delete_old_audit_events,
)
from goblinshift.platform.db import make_engine
from goblinshift.platform.settings import Settings
from tests.conftest import downgrade_migrations, run_migrations

pytestmark = pytest.mark.integration

NOW = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)

EXPECTED = {
    "object_use_object_id_idx": (
        "CREATE INDEX object_use_object_id_idx ON public.object_use USING btree (object_id)"
    ),
    "stored_object_attempt_id_idx": (
        "CREATE INDEX stored_object_attempt_id_idx ON public.stored_object USING btree "
        "(attempt_id) WHERE (attempt_id IS NOT NULL)"
    ),
    "web_session_ended_at_idx": (
        "CREATE INDEX web_session_ended_at_idx ON public.web_session USING btree "
        "(LEAST(revoked_at, idle_expires_at, absolute_expires_at))"
    ),
    "resource_reservation_released_at_idx": (
        "CREATE INDEX resource_reservation_released_at_idx ON public.resource_reservation "
        "USING btree (released_at) WHERE (released_at IS NOT NULL)"
    ),
}


@pytest.fixture
def engine(db_settings: Settings) -> Iterator[Engine]:
    engine = make_engine(db_settings)
    yield engine
    engine.dispose()
    run_migrations(db_settings, "head")


def index_definitions(engine: Engine) -> dict[str, str]:
    with engine.connect() as connection:
        rows = connection.execute(
            text("SELECT indexname, indexdef FROM pg_indexes WHERE indexname = ANY(:names)"),
            {"names": list(EXPECTED)},
        ).all()
    return {row.indexname: row.indexdef for row in rows}


def test_0019_cria_os_quatro_indices(db_settings: Settings, engine: Engine) -> None:
    run_migrations(db_settings, "0019")

    assert index_definitions(engine) == EXPECTED


def test_downgrade_da_0019_remove_os_indices(db_settings: Settings, engine: Engine) -> None:
    run_migrations(db_settings, "0019")

    downgrade_migrations(db_settings, "0018")

    assert index_definitions(engine) == {}


def test_limpeza_de_sessoes_usa_o_indice_do_fim(db_settings: Settings, engine: Engine) -> None:
    run_migrations(db_settings, "head")
    captured: list[tuple[str, Any]] = []

    def capture(
        _conn: Connection, _cursor: object, statement: str, parameters: Any, *_: object
    ) -> None:
        captured.append((statement, parameters))

    with engine.connect() as connection:
        # Tabela vazia: sem isto o planejador prefere a varredura sequencial, e o teste não
        # diria se o predicado casa com a expressão do índice.
        connection.exec_driver_sql("SET LOCAL enable_seqscan = off")
        event.listen(connection, "before_cursor_execute", capture)
        with Session(bind=connection) as session:
            delete_ended_web_sessions(session, NOW, 10)
        event.remove(connection, "before_cursor_execute", capture)
        statement, parameters = captured[-1]
        plan = connection.exec_driver_sql(f"EXPLAIN {statement}", parameters).scalars().all()
        connection.rollback()

    assert any("web_session_ended_at_idx" in line for line in plan), plan


def at_0018(settings: Settings) -> None:
    """Leva o schema a 0018 venha ele de baixo (banco novo) ou de cima (head)."""
    run_migrations(settings, "0018")
    downgrade_migrations(settings, "0018")


def new_user(
    conn: Connection, status: str, *, awaiting_approval: bool, age: str = "0 seconds"
) -> UUID:
    user_id: UUID = conn.execute(
        text(
            "INSERT INTO app_user (issuer, subject, display_name, status, awaiting_approval, "
            "created_at) VALUES ('teste', gen_random_uuid()::text, 'Ana', :status, :awaiting, "
            "now() - CAST(:age AS interval)) RETURNING id"
        ),
        {"status": status, "awaiting": awaiting_approval, "age": age},
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


def pending_flags(engine: Engine, user_ids: dict[str, UUID]) -> dict[str, bool]:
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT id, awaiting_approval FROM app_user WHERE id = ANY(:ids)"),
            {"ids": list(user_ids.values())},
        ).all()
    flag_by_id = {row.id: row.awaiting_approval for row in rows}
    return {name: flag_by_id[user_id] for name, user_id in user_ids.items()}


def accounts_left_by_the_old_api(engine: Engine) -> dict[str, UUID]:
    """Contas como a API antiga as grava entre a 0018 e a 0019: ela nunca escreve a marca."""
    with engine.begin() as conn:
        signup = new_user(conn, "blocked", awaiting_approval=False)
        blocked_while_pending = new_user(conn, "blocked", awaiting_approval=True)
        record_block(conn, blocked_while_pending)
        approved_while_pending = new_user(conn, "active", awaiting_approval=True)
        pending = new_user(conn, "blocked", awaiting_approval=True)
        blocked_for_abuse = new_user(conn, "blocked", awaiting_approval=False)
        record_block(conn, blocked_for_abuse)
        blocked_with_session = new_user(conn, "blocked", awaiting_approval=False)
        new_session(conn, blocked_with_session)
        active = new_user(conn, "active", awaiting_approval=False)
        # A auditoria guarda 90 dias: numa conta mais velha, o ACCOUNT_BLOCKED do bloqueio pelo
        # admin pode já ter saído, junto com as sessões. Sem rastro, ela não é da janela.
        blocked_before_retention = new_user(
            conn, "blocked", awaiting_approval=False, age="90 days 1 hour"
        )
        signup_inside_retention = new_user(
            conn, "blocked", awaiting_approval=False, age="89 days 23 hours"
        )
    return {
        "signup": signup,
        "blocked_while_pending": blocked_while_pending,
        "approved_while_pending": approved_while_pending,
        "pending": pending,
        "blocked_for_abuse": blocked_for_abuse,
        "blocked_with_session": blocked_with_session,
        "active": active,
        "blocked_before_retention": blocked_before_retention,
        "signup_inside_retention": signup_inside_retention,
    }


def test_prazo_da_marcacao_e_a_retencao_da_auditoria() -> None:
    # A 0019 só marca contas mais novas que a retenção: um prazo menor apagaria o ACCOUNT_BLOCKED
    # de contas que ela ainda olha. Mudar a retenção antes do deploy da 0019 pede rever o literal.
    assert timedelta(days=90) == AUDIT_RETENTION


NORMALIZED = {
    "signup": True,
    "blocked_while_pending": False,
    "approved_while_pending": False,
    "pending": True,
    "blocked_for_abuse": False,
    "blocked_with_session": False,
    "active": False,
    "blocked_before_retention": False,
    "signup_inside_retention": True,
}


def test_0019_normaliza_a_marca_deixada_pela_api_antiga(
    db_settings: Settings, engine: Engine
) -> None:
    at_0018(db_settings)
    accounts = accounts_left_by_the_old_api(engine)

    run_migrations(db_settings, "0019")

    assert pending_flags(engine, accounts) == NORMALIZED


def test_conta_criada_na_janela_entra_nos_pendentes(db_settings: Settings, engine: Engine) -> None:
    at_0018(db_settings)
    accounts = accounts_left_by_the_old_api(engine)

    run_migrations(db_settings, "0019")

    with Session(engine) as session:
        listed = {listing.id for listing in list_pending_accounts(session)}
    assert listed & set(accounts.values()) == {
        accounts["signup"],
        accounts["pending"],
        accounts["signup_inside_retention"],
    }


def test_rodar_a_0019_de_novo_nao_muda_a_marca(db_settings: Settings, engine: Engine) -> None:
    at_0018(db_settings)
    accounts = accounts_left_by_the_old_api(engine)
    run_migrations(db_settings, "0019")

    downgrade_migrations(db_settings, "0018")
    run_migrations(db_settings, "0019")

    assert pending_flags(engine, accounts) == NORMALIZED


def test_bloqueada_pelo_admin_e_expurgada_nao_volta_a_pendente(
    db_settings: Settings, engine: Engine
) -> None:
    # Caso da revisão, com datas coerentes: a auditoria só apaga o ACCOUNT_BLOCKED depois de 90
    # dias, então a conta bloqueada tem mais que isso. A limpeza roda com o relógio de agora.
    at_0018(db_settings)
    with engine.begin() as conn:
        user_id = new_user(conn, "active", awaiting_approval=False, age="100 days")
        new_session(conn, user_id)
    with Session(engine) as session, session.begin():
        assert block_account(session, user_id, datetime.now(UTC))
        record_block(session.connection(), user_id)
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE audit_event SET occurred_at = now() - interval '91 days' "
                "WHERE user_id = :id"
            ),
            {"id": user_id},
        )
        conn.execute(
            text(
                "UPDATE web_session SET revoked_at = now() - interval '91 days' WHERE user_id = :id"
            ),
            {"id": user_id},
        )
    now = datetime.now(UTC)
    with Session(engine) as session, session.begin():
        assert delete_ended_web_sessions(session, now, 100) >= 1
        assert delete_old_audit_events(session, now, 100) >= 1

    run_migrations(db_settings, "0019")

    assert pending_flags(engine, {"blocked": user_id}) == {"blocked": False}
    with Session(engine) as session:
        assert user_id not in {listing.id for listing in list_pending_accounts(session)}
