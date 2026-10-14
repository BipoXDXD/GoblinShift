"""Última etapa da purga da conta e retenção da identidade (GoblinShiftPlan.md, 1.8; Frente C)."""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid7

import pytest
from sqlalchemy import Table, func, insert, select
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.identity import AuditEventType, AuditOutcome, record_audit_event
from goblinshift.identity.accounts import request_account_deletion
from goblinshift.identity.purge import (
    AUDIT_RETENTION,
    WEB_SESSION_RETENTION,
    AccountNotMarkedForDeletionError,
    accounts_pending_purge,
    count_overdue_deletions,
    delete_ended_web_sessions,
    delete_expired_login_states,
    delete_old_audit_events,
    purge_owner,
)
from goblinshift.identity.tables import api_key, app_user, audit_event, login_state, web_session
from goblinshift.platform.db import unit_of_work
from tests.identity.support import new_key, new_user

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)
LIMIT = 500


def insert_web_session(
    factory: sessionmaker[Session],
    user_id: UUID,
    *,
    authenticated_at: datetime = NOW,
    revoked_at: datetime | None = None,
) -> UUID:
    with unit_of_work(factory) as session:
        session_id: UUID = session.execute(
            insert(web_session)
            .values(
                user_id=user_id,
                secret_hash=hashlib.sha256(secrets.token_bytes(32)).digest(),
                authenticated_at=authenticated_at,
                last_seen_at=authenticated_at,
                idle_expires_at=authenticated_at + timedelta(minutes=30),
                absolute_expires_at=authenticated_at + timedelta(hours=8),
                revoked_at=revoked_at,
            )
            .returning(web_session.c.id)
        ).scalar_one()
    return session_id


def request_deletion(factory: sessionmaker[Session], user_id: UUID, at: datetime = NOW) -> None:
    with unit_of_work(factory) as session:
        request_account_deletion(session, user_id, at)


def rows(factory: sessionmaker[Session], table: Table) -> int:
    with factory() as session:
        count: int = session.execute(select(func.count()).select_from(table)).scalar_one()
    return count


# --- purge_owner ------------------------------------------------------------------------------


def test_purga_apaga_conta_chaves_e_sessoes(factory: sessionmaker[Session]) -> None:
    user_id = new_user(factory)
    new_key(factory, user_id, now=NOW)
    insert_web_session(factory, user_id)
    request_deletion(factory, user_id)

    with unit_of_work(factory) as session:
        purge_owner(session, user_id)

    assert rows(factory, app_user) == 0
    assert rows(factory, api_key) == 0
    assert rows(factory, web_session) == 0


def test_auditoria_sobrevive_a_purga_sem_identificar(factory: sessionmaker[Session]) -> None:
    user_id = new_user(factory)
    with unit_of_work(factory) as session:
        record_audit_event(
            session,
            event=AuditEventType.ACCOUNT_DELETION_REQUESTED,
            user_id=user_id,
            ip="203.0.113.77",
            outcome=AuditOutcome.SUCCESS,
            now=NOW,
        )
    request_deletion(factory, user_id)

    with unit_of_work(factory) as session:
        purge_owner(session, user_id)

    with factory() as session:
        [event] = session.execute(select(audit_event)).all()
    assert event.user_id == user_id
    assert str(event.ip_prefix) == "203.0.113.0/24"
    # O evento não guarda nome nem identidade externa: sem `app_user`, o UUID não leva a ninguém.
    assert set(event._mapping) == {
        "id",
        "user_id",
        "event_type",
        "outcome",
        "ip_prefix",
        "occurred_at",
    }


def test_purgar_de_novo_e_sucesso(factory: sessionmaker[Session]) -> None:
    user_id = new_user(factory)
    request_deletion(factory, user_id)
    with unit_of_work(factory) as session:
        purge_owner(session, user_id)

    with unit_of_work(factory) as session:
        purge_owner(session, user_id)

    assert rows(factory, app_user) == 0


def test_conta_sem_pedido_de_exclusao_nunca_e_purgada(factory: sessionmaker[Session]) -> None:
    user_id = new_user(factory)
    new_key(factory, user_id, now=NOW)

    with pytest.raises(AccountNotMarkedForDeletionError), unit_of_work(factory) as session:
        purge_owner(session, user_id)

    assert rows(factory, app_user) == 1
    assert rows(factory, api_key) == 1


def test_purga_nao_toca_outra_conta(factory: sessionmaker[Session]) -> None:
    gone, kept = new_user(factory), new_user(factory)
    new_key(factory, kept, now=NOW)
    insert_web_session(factory, kept)
    request_deletion(factory, gone)

    with unit_of_work(factory) as session:
        purge_owner(session, gone)

    with factory() as session:
        assert session.execute(select(app_user.c.id)).scalars().all() == [kept]
    assert rows(factory, api_key) == 1
    assert rows(factory, web_session) == 1


# --- Contas a purgar --------------------------------------------------------------------------


def test_contas_a_purgar_sao_as_com_pedido_mais_antigas_primeiro(
    factory: sessionmaker[Session],
) -> None:
    new_user(factory)
    newer, older = new_user(factory), new_user(factory)
    request_deletion(factory, newer, NOW)
    request_deletion(factory, older, NOW - timedelta(hours=1))

    with factory() as session:
        assert accounts_pending_purge(session, limit=10) == [older, newer]
        assert accounts_pending_purge(session, limit=1) == [older]


def test_exclusao_atrasada_conta_a_partir_de_20_horas(factory: sessionmaker[Session]) -> None:
    on_time, at_limit, late = new_user(factory), new_user(factory), new_user(factory)
    request_deletion(factory, on_time, NOW - timedelta(hours=20) + timedelta(seconds=1))
    request_deletion(factory, at_limit, NOW - timedelta(hours=20))
    request_deletion(factory, late, NOW - timedelta(hours=23))

    with factory() as session:
        assert count_overdue_deletions(session, NOW) == 2


# --- Retenção ---------------------------------------------------------------------------------


def test_estado_de_login_vencido_sai_e_o_valido_fica(factory: sessionmaker[Session]) -> None:
    with unit_of_work(factory) as session:
        session.execute(
            insert(login_state),
            [
                {"key": "vencido", "value": "{}", "expires_at": NOW - timedelta(seconds=1)},
                {"key": "no-limite", "value": "{}", "expires_at": NOW},
                {"key": "valido", "value": "{}", "expires_at": NOW + timedelta(seconds=1)},
            ],
        )

    with unit_of_work(factory) as session:
        removed = delete_expired_login_states(session, NOW, LIMIT)

    assert removed == 2
    with factory() as session:
        assert session.execute(select(login_state.c.key)).scalars().all() == ["valido"]


def test_sessao_sai_7_dias_depois_do_fim(factory: sessionmaker[Session]) -> None:
    user_id = new_user(factory)
    # Fim = o primeiro entre revogação, inatividade (30 min) e teto (8 h).
    revoked_old = insert_web_session(
        factory,
        user_id,
        authenticated_at=NOW - timedelta(days=8),
        revoked_at=NOW - WEB_SESSION_RETENTION - timedelta(seconds=1),
    )
    idle_old = insert_web_session(
        factory, user_id, authenticated_at=NOW - WEB_SESSION_RETENTION - timedelta(minutes=31)
    )
    idle_recent = insert_web_session(
        factory, user_id, authenticated_at=NOW - WEB_SESSION_RETENTION - timedelta(minutes=29)
    )
    active = insert_web_session(factory, user_id, authenticated_at=NOW)

    with unit_of_work(factory) as session:
        removed = delete_ended_web_sessions(session, NOW, LIMIT)

    assert removed == 2
    with factory() as session:
        left = set(session.execute(select(web_session.c.id)).scalars())
    assert left == {idle_recent, active}
    assert {revoked_old, idle_old}.isdisjoint(left)


def test_auditoria_sai_depois_de_90_dias(factory: sessionmaker[Session]) -> None:
    with unit_of_work(factory) as session:
        for occurred_at in (
            NOW - AUDIT_RETENTION - timedelta(seconds=1),
            NOW - AUDIT_RETENTION,
            NOW - AUDIT_RETENTION + timedelta(seconds=1),
        ):
            record_audit_event(
                session,
                event=AuditEventType.LOGOUT,
                user_id=uuid7(),
                ip=None,
                outcome=AuditOutcome.SUCCESS,
                now=occurred_at,
            )

    with unit_of_work(factory) as session:
        removed = delete_old_audit_events(session, NOW, LIMIT)

    # "Mais de 90 dias": o evento de exatamente 90 dias ainda fica.
    assert removed == 1
    assert rows(factory, audit_event) == 2


def test_retencao_respeita_o_tamanho_do_lote(factory: sessionmaker[Session]) -> None:
    with unit_of_work(factory) as session:
        session.execute(
            insert(login_state),
            [
                {"key": f"k{i}", "value": "{}", "expires_at": NOW - timedelta(hours=1)}
                for i in range(3)
            ],
        )

    with unit_of_work(factory) as session:
        assert delete_expired_login_states(session, NOW, 2) == 2
    with unit_of_work(factory) as session:
        assert delete_expired_login_states(session, NOW, 2) == 1
