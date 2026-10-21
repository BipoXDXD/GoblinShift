"""Migração 0016: auditoria do `gs-admin set-limits` (`ACCOUNT_LIMITS_CHANGED` e `details`).

Expand: o CHECK de `audit_event.event_type` aceita o tipo novo, e a coluna `details` (jsonb,
anulável) guarda os valores antigos e novos. Um CHECK amarra os dois: só esse tipo tem detalhe, e
ele sempre o tem, como objeto. O downgrade apaga esses eventos, que o CHECK antigo recusaria, e a
coluna.
"""

from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, inspect, text
from sqlalchemy.exc import IntegrityError

from goblinshift.platform.db import make_engine
from goblinshift.platform.settings import Settings
from tests.conftest import downgrade_migrations, run_migrations

pytestmark = pytest.mark.integration

CHANGES = '{"changes": {"runs_per_24h": {"old": null, "new": 5}}}'


@pytest.fixture
def engine(db_settings: Settings) -> Iterator[Engine]:
    engine = make_engine(db_settings)
    yield engine
    engine.dispose()
    run_migrations(db_settings, "head")


def record(engine: Engine, event_type: str, details: str | None) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO audit_event (event_type, outcome, details) "
                "VALUES (:event_type, 'success', CAST(:details AS jsonb))"
            ),
            {"event_type": event_type, "details": details},
        )


def count(engine: Engine, event_type: str) -> int:
    with engine.connect() as conn:
        found: int = conn.execute(
            text("SELECT count(*) FROM audit_event WHERE event_type = :event_type"),
            {"event_type": event_type},
        ).scalar_one()
    return found


def test_0016_aceita_a_mudanca_de_limites_com_detalhe(
    db_settings: Settings, engine: Engine
) -> None:
    run_migrations(db_settings, "0016")

    before = count(engine, "ACCOUNT_LIMITS_CHANGED")

    record(engine, "ACCOUNT_LIMITS_CHANGED", CHANGES)

    assert count(engine, "ACCOUNT_LIMITS_CHANGED") == before + 1


@pytest.mark.parametrize(
    ("event_type", "details"),
    [
        ("ACCOUNT_LIMITS_CHANGED", None),
        ("ACCOUNT_LIMITS_CHANGED", "[]"),
        ("ACCOUNT_LIMITS_CHANGED", "null"),
        ("ACCOUNT_BLOCKED", CHANGES),
    ],
    ids=["sem-detalhe", "lista", "json-null", "detalhe-em-outro-tipo"],
)
def test_detalhe_so_na_mudanca_de_limites_e_sempre_objeto(
    db_settings: Settings, engine: Engine, event_type: str, details: str | None
) -> None:
    run_migrations(db_settings, "0016")

    with pytest.raises(IntegrityError, match="audit_event_details_check"):
        record(engine, event_type, details)


def test_outros_eventos_continuam_sem_detalhe(db_settings: Settings, engine: Engine) -> None:
    run_migrations(db_settings, "0016")

    before = count(engine, "ACCOUNT_BLOCKED")

    record(engine, "ACCOUNT_BLOCKED", None)

    assert count(engine, "ACCOUNT_BLOCKED") == before + 1


def test_downgrade_apaga_os_eventos_e_a_coluna_e_restaura_o_check(
    db_settings: Settings, engine: Engine
) -> None:
    run_migrations(db_settings, "0016")
    record(engine, "ACCOUNT_LIMITS_CHANGED", CHANGES)
    record(engine, "ACCOUNT_BLOCKED", None)
    blocked = count(engine, "ACCOUNT_BLOCKED")  # o banco não é limpo entre estes testes

    downgrade_migrations(db_settings, "0015")

    columns = {c["name"] for c in inspect(engine).get_columns("audit_event")}
    assert "details" not in columns
    assert count(engine, "ACCOUNT_LIMITS_CHANGED") == 0
    assert count(engine, "ACCOUNT_BLOCKED") == blocked
    with (
        pytest.raises(IntegrityError, match="audit_event_event_type_check"),
        engine.begin() as conn,
    ):
        conn.execute(
            text(
                "INSERT INTO audit_event (event_type, outcome) "
                "VALUES ('ACCOUNT_LIMITS_CHANGED', 'success')"
            )
        )
