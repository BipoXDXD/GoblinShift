"""Auditoria de eventos de segurança (docs/GoblinShiftPlan.md, 1.8 e 3.6; ADR 026)."""

from datetime import UTC, datetime, timedelta
from ipaddress import ip_network
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.identity.audit import (
    AuditEventType,
    AuditOutcome,
    record,
    record_anonymous_login_failure,
    record_csrf_failure,
    truncate_ip,
)
from goblinshift.identity.tables import audit_event
from goblinshift.platform.db import unit_of_work

AGORA = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)
CONTA = UUID("00000000-0000-7000-8000-0000000000bb")
OUTRA_CONTA = UUID("00000000-0000-7000-8000-0000000000cc")


# --- truncate_ip (unit) -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("ip", "prefix"),
    [
        ("203.0.113.7", "203.0.113.0/24"),
        ("203.0.113.0", "203.0.113.0/24"),
        ("203.0.113.255", "203.0.113.0/24"),
        ("2001:db8:abcd:12:1::7", "2001:db8:abcd::/48"),
        ("2001:db8:abcd::", "2001:db8:abcd::/48"),
        # IPv4 mapeado em IPv6 (socket dual-stack) é o mesmo cliente IPv4: /24, não /48.
        ("::ffff:203.0.113.7", "203.0.113.0/24"),
        ("  203.0.113.7  ", "203.0.113.0/24"),
    ],
)
def test_ip_truncado_em_24_no_v4_e_48_no_v6(ip: str, prefix: str) -> None:
    assert truncate_ip(ip) == ip_network(prefix)


@pytest.mark.parametrize(
    "ip", [None, "", "testclient", "203.0.113", "203.0.113.7/32", "1.2.3.4\x00", "::g"]
)
def test_ip_ausente_ou_invalido_vira_none(ip: str | None) -> None:
    assert truncate_ip(ip) is None


def test_tipos_de_evento_do_plano() -> None:
    assert {event.value for event in AuditEventType} == {
        "LOGIN_SUCCEEDED",
        "LOGIN_FAILED",
        "LOGOUT",
        "LOGOUT_ALL",
        "KEY_ISSUED",
        "KEY_REVOKED",
        "REAUTH_REQUIRED",
        "CSRF_FAILED",
        "ACCESS_DENIED",
        "ACCOUNT_DELETION_REQUESTED",
        "ACCOUNT_BLOCKED",
        "ACCOUNT_UNBLOCKED",
        "TERMS_ACCEPTED",
        "ACCOUNT_LIMITS_CHANGED",
    }


# --- record (integração) ----------------------------------------------------------------------


@pytest.mark.integration
def test_record_grava_o_evento_com_o_ip_truncado(factory: sessionmaker[Session]) -> None:
    with unit_of_work(factory) as session:
        record(
            session,
            event=AuditEventType.CSRF_FAILED,
            user_id=CONTA,
            ip="203.0.113.7",
            outcome=AuditOutcome.FAILURE,
            now=AGORA,
        )

    with unit_of_work(factory) as session:
        row = session.execute(select(audit_event).where(audit_event.c.user_id == CONTA)).one()

    assert row.event_type == "CSRF_FAILED"
    assert row.outcome == "failure"
    assert row.ip_prefix == ip_network("203.0.113.0/24")
    assert row.occurred_at == AGORA


@pytest.mark.integration
def test_record_aceita_evento_sem_conta_e_sem_ip(factory: sessionmaker[Session]) -> None:
    with unit_of_work(factory) as session:
        record(
            session,
            event=AuditEventType.LOGIN_FAILED,
            user_id=None,
            ip=None,
            outcome=AuditOutcome.FAILURE,
            now=AGORA,
        )

    with unit_of_work(factory) as session:
        rows = session.execute(
            select(audit_event.c.user_id, audit_event.c.ip_prefix).where(
                audit_event.c.event_type == "LOGIN_FAILED"
            )
        ).all()

    assert [(row.user_id, row.ip_prefix) for row in rows] == [(None, None)]


# --- record_csrf_failure (integração) ---------------------------------------------------------


def _csrf_failure(factory: sessionmaker[Session], user_id: UUID, now: datetime) -> bool:
    with unit_of_work(factory) as session:
        return record_csrf_failure(session, user_id=user_id, ip="203.0.113.7", now=now)


def _csrf_failed_rows(factory: sessionmaker[Session]) -> list[tuple[UUID, datetime]]:
    with unit_of_work(factory) as session:
        rows = session.execute(
            select(audit_event.c.user_id, audit_event.c.occurred_at)
            .where(audit_event.c.event_type == "CSRF_FAILED")
            .order_by(audit_event.c.id)
        ).all()
    return [(row.user_id, row.occurred_at) for row in rows]


@pytest.mark.integration
def test_csrf_failed_grava_uma_vez_por_minuto(factory: sessionmaker[Session]) -> None:
    gravou = [
        _csrf_failure(factory, CONTA, AGORA + timedelta(seconds=seconds)) for seconds in (0, 30, 59)
    ]

    assert gravou == [True, False, False]
    assert _csrf_failed_rows(factory) == [(CONTA, AGORA)]


@pytest.mark.integration
def test_csrf_failed_com_um_minuto_exato_grava_de_novo(factory: sessionmaker[Session]) -> None:
    # A janela é `occurred_at > now - 1 min`: no minuto exato, a anterior já ficou de fora.
    _csrf_failure(factory, CONTA, AGORA)

    assert _csrf_failure(factory, CONTA, AGORA + timedelta(minutes=1)) is True
    assert _csrf_failed_rows(factory) == [(CONTA, AGORA), (CONTA, AGORA + timedelta(minutes=1))]


@pytest.mark.integration
def test_agregacao_do_csrf_failed_e_por_conta(factory: sessionmaker[Session]) -> None:
    _csrf_failure(factory, CONTA, AGORA)

    assert _csrf_failure(factory, OUTRA_CONTA, AGORA + timedelta(seconds=1)) is True
    assert _csrf_failed_rows(factory) == [
        (CONTA, AGORA),
        (OUTRA_CONTA, AGORA + timedelta(seconds=1)),
    ]


@pytest.mark.integration
def test_csrf_failed_agregado_grava_as_mesmas_colunas_do_record(
    factory: sessionmaker[Session],
) -> None:
    _csrf_failure(factory, CONTA, AGORA)

    with unit_of_work(factory) as session:
        row = session.execute(select(audit_event).where(audit_event.c.user_id == CONTA)).one()

    assert (row.event_type, row.outcome, row.ip_prefix, row.request_id, row.details) == (
        "CSRF_FAILED",
        "failure",
        ip_network("203.0.113.0/24"),
        None,
        None,
    )


# --- record_anonymous_login_failure (integração) ----------------------------------------------

REF = UUID("00000000-0000-7000-8000-0000000000dd")


def _anonymous_failure(factory: sessionmaker[Session], now: datetime, limit: int = 3) -> bool:
    with unit_of_work(factory) as session:
        return record_anonymous_login_failure(
            session, ip="203.0.113.7", request_id=REF, now=now, limit=limit
        )


def _login_failed_rows(factory: sessionmaker[Session]) -> list[tuple[UUID | None, datetime]]:
    with unit_of_work(factory) as session:
        rows = session.execute(
            select(audit_event.c.user_id, audit_event.c.occurred_at)
            .where(audit_event.c.event_type == "LOGIN_FAILED")
            .order_by(audit_event.c.id)
        ).all()
    return [(row.user_id, row.occurred_at) for row in rows]


def _account_failure(factory: sessionmaker[Session], now: datetime) -> None:
    with unit_of_work(factory) as session:
        record(
            session,
            event=AuditEventType.LOGIN_FAILED,
            user_id=CONTA,
            ip="203.0.113.7",
            outcome=AuditOutcome.FAILURE,
            now=now,
        )


@pytest.mark.integration
def test_login_failed_anonimo_para_no_teto_do_minuto(factory: sessionmaker[Session]) -> None:
    gravou = [
        _anonymous_failure(factory, AGORA + timedelta(seconds=seconds)) for seconds in (0, 1, 2, 3)
    ]

    assert gravou == [True, True, True, False]
    assert len(_login_failed_rows(factory)) == 3


@pytest.mark.integration
def test_teto_anonimo_reabre_depois_de_um_minuto(factory: sessionmaker[Session]) -> None:
    # A janela é `occurred_at > now - 1 min`: no minuto exato, a primeira já ficou de fora.
    for seconds in (0, 1, 2, 3):
        _anonymous_failure(factory, AGORA + timedelta(seconds=seconds))

    assert _anonymous_failure(factory, AGORA + timedelta(minutes=1)) is True
    assert len(_login_failed_rows(factory)) == 4


@pytest.mark.integration
def test_teto_anonimo_nao_conta_recusa_de_conta(factory: sessionmaker[Session]) -> None:
    for _ in range(3):
        _account_failure(factory, AGORA)

    assert _anonymous_failure(factory, AGORA + timedelta(seconds=1)) is True


@pytest.mark.integration
def test_login_failed_anonimo_grava_as_mesmas_colunas_do_record(
    factory: sessionmaker[Session],
) -> None:
    _anonymous_failure(factory, AGORA)

    with unit_of_work(factory) as session:
        row = session.execute(select(audit_event)).one()

    assert (row.event_type, row.outcome, row.user_id, row.ip_prefix, row.request_id) == (
        "LOGIN_FAILED",
        "failure",
        None,
        ip_network("203.0.113.0/24"),
        REF,
    )
    assert (row.occurred_at, row.details) == (AGORA, None)
