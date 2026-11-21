"""Dois `set-limits` simultâneos na mesma conta (1.7; ADR 026): nenhum perde a mudança do outro.

O override das cotas regrava as duas colunas a partir do que leu; sem serialização, o segundo
comando leria o estado anterior ao primeiro, apagaria a mudança dele e auditaria `old` errado.
A primeira sessão segura a transação aberta até a segunda esperar a trava.
"""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.identity.tables import audit_event
from goblinshift.platform.db import unit_of_work
from goblinshift.runs.limits import change_account_limits
from goblinshift.runs.quotas import QuotaOverride, quota_override
from tests.lock_support import in_thread, wait_until_someone_waits_for_a_lock
from tests.runs.support import new_owner

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 16, 12, 0, tzinfo=UTC)


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
            second = in_thread(lambda: _change(factory, owner, {"input_bytes_per_24h": 1000}))
            wait_until_someone_waits_for_a_lock(factory, "o segundo set-limits")
        second.result()
    finally:
        first.close()

    with factory() as session:
        assert quota_override(session, owner) == QuotaOverride(5, 1000)
    assert limits_events(factory, owner) == [
        {"changes": {"runs_per_24h": {"old": None, "new": 5}}},
        {"changes": {"input_bytes_per_24h": {"old": None, "new": 1000}}},
    ]


def _change(factory: sessionmaker[Session], owner: uuid.UUID, limits: dict[str, int]) -> None:
    with unit_of_work(factory) as session:
        change_account_limits(session, owner, limits, NOW)
