"""Migração 0008: o evento `TERMS_ACCEPTED` na auditoria (ADR 029).

Expand: o CHECK de `audit_event.event_type` passa a aceitar o tipo novo, que o código anterior
nunca grava. O downgrade apaga esses eventos antes de restaurar o CHECK antigo, que os recusaria.
"""

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine, func, insert, select
from sqlalchemy.exc import IntegrityError

from goblinshift.identity.tables import audit_event
from goblinshift.platform.db import make_engine
from goblinshift.platform.settings import Settings
from tests.conftest import downgrade_migrations, run_migrations

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)


@pytest.fixture
def engine(db_settings: Settings) -> Iterator[Engine]:
    engine = make_engine(db_settings)
    yield engine
    engine.dispose()
    run_migrations(db_settings, "head")


def record_terms_event(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(
            insert(audit_event).values(
                event_type="TERMS_ACCEPTED", outcome="success", occurred_at=NOW
            )
        )


def terms_events(engine: Engine) -> int:
    with engine.connect() as conn:
        count: int = conn.execute(
            select(func.count())
            .select_from(audit_event)
            .where(audit_event.c.event_type == "TERMS_ACCEPTED")
        ).scalar_one()
    return count


def test_0008_aceita_o_evento_de_aceite(db_settings: Settings, engine: Engine) -> None:
    run_migrations(db_settings, "0008")

    record_terms_event(engine)

    assert terms_events(engine) >= 1


def test_downgrade_apaga_os_eventos_de_aceite_e_restaura_o_check(
    db_settings: Settings, engine: Engine
) -> None:
    run_migrations(db_settings, "0008")
    record_terms_event(engine)

    downgrade_migrations(db_settings, "0007")

    assert terms_events(engine) == 0
    with pytest.raises(IntegrityError, match="audit_event_event_type_check"):
        record_terms_event(engine)
