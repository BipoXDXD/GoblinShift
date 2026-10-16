"""Dois `set-limits` simultâneos na mesma conta (1.7; ADR 026): nenhum perde a mudança do outro.

O override das cotas regrava as duas colunas a partir do que leu; sem serialização, o segundo
comando leria o estado anterior ao primeiro, apagaria a mudança dele e auditaria `old` errado.
A primeira sessão segura a transação aberta até a segunda esperar a trava.
"""

import threading
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.identity.tables import audit_event
from goblinshift.platform.db import unit_of_work
from goblinshift.runs.limits import change_account_limits
from goblinshift.runs.quotas import QuotaOverride, quota_override
from tests.runs.support import new_owner

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 16, 12, 0, tzinfo=UTC)
# Abaixo do `lock_timeout` de 2 s do `make_engine`: a segunda sessão ainda está esperando.
LOCK_WAIT_DEADLINE_S = 1.5


def wait_until_someone_waits_for_a_lock(factory: sessionmaker[Session]) -> None:
    deadline = time.monotonic() + LOCK_WAIT_DEADLINE_S
    with factory() as session:
        while time.monotonic() < deadline:
            waiting = session.execute(
                text(
                    "SELECT count(*) FROM pg_stat_activity"
                    " WHERE datname = current_database() AND wait_event_type = 'Lock'"
                )
            ).scalar_one()
            session.rollback()
            if waiting:
                return
            threading.Event().wait(0.01)
    raise AssertionError("o segundo set-limits não chegou a esperar a trava da conta")


def in_thread(work: Callable[[], None]) -> tuple[threading.Thread, list[BaseException]]:
    errors: list[BaseException] = []

    def run() -> None:
        try:
            work()
        except BaseException as error:  # devolvido ao teste, que o relança
            errors.append(error)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread, errors


def limits_events(factory: sessionmaker[Session], owner: uuid.UUID) -> list[object]:
    with factory() as session:
        return list(
            session.execute(
                select(audit_event.c.details)
                .where(
                    audit_event.c.user_id == owner,
                    audit_event.c.event_type == "ACCOUNT_LIMITS_CHANGED",
                )
                .order_by(audit_event.c.id)
            ).scalars()
        )


def test_set_limits_simultaneos_na_mesma_conta_nao_perdem_mudanca(
    factory: sessionmaker[Session],
) -> None:
    owner = new_owner(factory)
    first = factory()
    try:
        with first.begin():
            change_account_limits(first, owner, {"runs_per_24h": 5}, NOW)
            second, errors = in_thread(
                lambda: _change(factory, owner, {"input_bytes_per_24h": 1000})
            )
            wait_until_someone_waits_for_a_lock(factory)
        second.join(timeout=5)
    finally:
        first.close()

    assert not second.is_alive()
    assert errors == []
    with factory() as session:
        assert quota_override(session, owner) == QuotaOverride(5, 1000)
    assert limits_events(factory, owner) == [
        {"changes": {"runs_per_24h": {"old": None, "new": 5}}},
        {"changes": {"input_bytes_per_24h": {"old": None, "new": 1000}}},
    ]


def _change(factory: sessionmaker[Session], owner: uuid.UUID, limits: dict[str, int]) -> None:
    with unit_of_work(factory) as session:
        change_account_limits(session, owner, limits, NOW)
