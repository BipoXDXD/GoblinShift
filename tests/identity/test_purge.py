"""Última etapa da purga da conta e retenção da identidade (GoblinShiftPlan.md, 1.8; Frente C)."""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid7

import pytest
from sqlalchemy import Table, func, insert, select, text, update
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.identity import AuditEventType, AuditOutcome, record_audit_event
from goblinshift.identity.accounts import KEY_RETENTION, request_account_deletion
from goblinshift.identity.purge import (
    AUDIT_RETENTION,
    WEB_SESSION_RETENTION,
    AccountNotMarkedForDeletionError,
    claim_accounts_for_purge,
    count_overdue_audit_events,
    count_overdue_deletions,
    delete_ended_web_sessions,
    delete_expired_login_states,
    delete_inactive_api_keys,
    delete_old_audit_events,
    estimate_audit_events,
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
    # O `request_id` é aleatório por requisição e também não identifica ninguém; `details` só
    # guarda números de limites (migração 0016).
    assert set(event._mapping) == {
        "id",
        "user_id",
        "event_type",
        "outcome",
        "ip_prefix",
        "occurred_at",
        "request_id",
        "details",
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

    with unit_of_work(factory) as session:
        assert claim_accounts_for_purge(session, limit=10) == [older, newer]


def test_conta_reivindicada_vai_para_o_fim_da_fila(factory: sessionmaker[Session]) -> None:
    """#442: sem o rodízio, a mais antiga voltaria a cada ciclo e a outra nunca seria tentada."""
    newer, older = new_user(factory), new_user(factory)
    request_deletion(factory, newer, NOW)
    request_deletion(factory, older, NOW - timedelta(hours=1))

    claimed: list[UUID] = []
    for _ in range(3):
        with unit_of_work(factory) as session:
            claimed.extend(claim_accounts_for_purge(session, limit=1))

    assert claimed == [older, newer, older]


def test_reivindicar_marca_a_tentativa_so_nas_contas_escolhidas(
    factory: sessionmaker[Session],
) -> None:
    untouched, newer, older = new_user(factory), new_user(factory), new_user(factory)
    request_deletion(factory, newer, NOW)
    request_deletion(factory, older, NOW - timedelta(hours=1))

    with unit_of_work(factory) as session:
        claim_accounts_for_purge(session, limit=1)

    with factory() as session:
        attempted = {
            row.id: row.purge_attempted_at
            for row in session.execute(select(app_user.c.id, app_user.c.purge_attempted_at))
        }
    assert attempted[older] is not None
    assert attempted[newer] is None
    assert attempted[untouched] is None


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


def revoked_key(
    factory: sessionmaker[Session], user_id: UUID, *, issued_at: datetime, revoked_at: datetime
) -> UUID:
    issued = new_key(factory, user_id, now=issued_at, days=90)
    with unit_of_work(factory) as session:
        session.execute(
            update(api_key).where(api_key.c.id == issued.id).values(revoked_at=revoked_at)
        )
    return issued.id


def key_ids(factory: sessionmaker[Session]) -> set[UUID]:
    with factory() as session:
        return set(session.execute(select(api_key.c.id)).scalars())


def test_chave_revogada_ha_30_dias_sai_e_a_de_29_fica(factory: sessionmaker[Session]) -> None:
    user_id = new_user(factory)
    issued_at = NOW - timedelta(days=40)
    # Fim = o primeiro entre revogação e vencimento; "mais de 30 dias" é estrito.
    revoked_old = revoked_key(
        factory, user_id, issued_at=issued_at, revoked_at=NOW - timedelta(days=30, seconds=1)
    )
    revoked_at_limit = revoked_key(
        factory, user_id, issued_at=issued_at, revoked_at=NOW - KEY_RETENTION
    )
    revoked_recent = revoked_key(
        factory, user_id, issued_at=issued_at, revoked_at=NOW - timedelta(days=29)
    )
    # Venceu há 31 dias e foi revogada depois: conta o vencimento.
    expired_then_revoked = new_key(factory, user_id, now=NOW - timedelta(days=61), days=30)
    with unit_of_work(factory) as session:
        session.execute(
            update(api_key)
            .where(api_key.c.id == expired_then_revoked.id)
            .values(revoked_at=NOW - timedelta(days=1))
        )
    expired_old = new_key(factory, user_id, now=NOW - timedelta(days=61), days=30).id
    expired_recent = new_key(factory, user_id, now=NOW - timedelta(days=59), days=30).id

    with unit_of_work(factory) as session:
        removed = delete_inactive_api_keys(session, NOW, LIMIT)

    assert removed == 3
    assert key_ids(factory) == {revoked_at_limit, revoked_recent, expired_recent}
    assert {revoked_old, expired_then_revoked.id, expired_old}.isdisjoint(key_ids(factory))


def test_chave_ativa_nunca_sai_pela_retencao(factory: sessionmaker[Session]) -> None:
    user_id = new_user(factory)
    # Emitida há 89 dias com validade de 90 e nunca usada: ainda autentica.
    active = new_key(factory, user_id, now=NOW - timedelta(days=89), days=90).id

    with unit_of_work(factory) as session:
        removed = delete_inactive_api_keys(session, NOW, LIMIT)

    assert removed == 0
    assert key_ids(factory) == {active}


def test_retencao_de_chaves_respeita_o_tamanho_do_lote(factory: sessionmaker[Session]) -> None:
    user_id = new_user(factory)
    for _ in range(3):
        new_key(factory, user_id, now=NOW - timedelta(days=61), days=30)

    with unit_of_work(factory) as session:
        assert delete_inactive_api_keys(session, NOW, 2) == 2
    with unit_of_work(factory) as session:
        assert delete_inactive_api_keys(session, NOW, 2) == 1


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


def _audit_at(factory: sessionmaker[Session], *occurred_at: datetime) -> None:
    with unit_of_work(factory) as session:
        for moment in occurred_at:
            record_audit_event(
                session,
                event=AuditEventType.LOGOUT,
                user_id=uuid7(),
                ip=None,
                outcome=AuditOutcome.SUCCESS,
                now=moment,
            )


@pytest.mark.parametrize(
    ("age", "overdue"),
    [
        (AUDIT_RETENTION + timedelta(days=1), 0),
        (AUDIT_RETENTION + timedelta(days=1, seconds=1), 1),
    ],
    ids=["91_dias", "91_dias_e_1_s"],
)
def test_atraso_da_auditoria_comeca_depois_de_91_dias(
    factory: sessionmaker[Session], age: timedelta, overdue: int
) -> None:
    # Atraso é passar mais de um dia da retenção de 90: 91 dias ainda não, 91 dias e 1 s sim.
    _audit_at(factory, NOW - age)

    with factory() as session:
        assert count_overdue_audit_events(session, NOW) == overdue


def test_estimativa_da_auditoria_le_as_estatisticas_sem_varrer(
    factory: sessionmaker[Session],
) -> None:
    _audit_at(factory, NOW, NOW, NOW)
    with factory() as session:
        session.execute(text("ANALYZE audit_event"))

    with factory() as session:
        assert estimate_audit_events(session) == 3


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


def insert_expired_login_states(factory: sessionmaker[Session], count: int) -> None:
    with unit_of_work(factory) as session:
        session.execute(
            insert(login_state),
            [
                {"key": f"k{i}", "value": "{}", "expires_at": NOW - timedelta(hours=1)}
                for i in range(count)
            ],
        )


def test_lote_pula_linhas_travadas_e_nao_passa_do_teto(factory: sessionmaker[Session]) -> None:
    insert_expired_login_states(factory, 6)

    with factory() as holder, holder.begin():
        holder.execute(
            select(login_state.c.key).where(login_state.c.key.in_(["k0", "k1"])).with_for_update()
        ).all()
        with unit_of_work(factory) as session:
            removed = delete_expired_login_states(session, NOW, 2)

    assert removed == 2
    with factory() as session:
        left = set(session.execute(select(login_state.c.key)).scalars())
    assert len(left) == 4
    assert {"k0", "k1"} <= left
