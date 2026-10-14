"""Auditoria de eventos de segurança (docs/GoblinShiftPlan.md, 1.8 e 3.6; ADR 026)."""

from datetime import UTC, datetime
from ipaddress import ip_network
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.identity.audit import AuditEventType, AuditOutcome, record, truncate_ip
from goblinshift.identity.tables import audit_event
from goblinshift.platform.db import unit_of_work

AGORA = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)
CONTA = UUID("00000000-0000-7000-8000-0000000000bb")


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
