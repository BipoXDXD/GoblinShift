"""Migração 0030: valida o CHECK `attempt_yielded_after_start_check` criado na 0029."""

from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, inspect, text

from goblinshift.platform.db import make_engine
from goblinshift.platform.settings import Settings
from tests.conftest import downgrade_migrations, run_migrations, stamp_migrations
from tests.run_finish_migration_support import at_revision

pytestmark = pytest.mark.integration

CHECK_NAME = "attempt_yielded_after_start_check"


@pytest.fixture
def engine(db_settings: Settings) -> Iterator[Engine]:
    engine = make_engine(db_settings)
    yield engine
    engine.dispose()
    run_migrations(db_settings, "head")


def _convalidated(engine: Engine) -> bool | None:
    with engine.connect() as conn:
        return conn.execute(
            text(
                "SELECT convalidated FROM pg_constraint "
                "WHERE conrelid = 'attempt'::regclass AND conname = :name"
            ),
            {"name": CHECK_NAME},
        ).scalar_one_or_none()


def test_0030_valida_check_de_tentativa_cedida(db_settings: Settings, engine: Engine) -> None:
    at_revision(db_settings, "0028")
    run_migrations(db_settings, "0029")
    assert _convalidated(engine) is False

    run_migrations(db_settings, "0030")

    assert _convalidated(engine) is True


def test_0030_repetida_depois_de_aplicada_termina_igual(
    db_settings: Settings, engine: Engine
) -> None:
    at_revision(db_settings, "0028")
    run_migrations(db_settings, "0029")
    run_migrations(db_settings, "0030")
    stamp_migrations(db_settings, "0029")

    run_migrations(db_settings, "0030")

    assert _convalidated(engine) is True


def test_downgrade_da_0030_mantem_coluna_e_check_compativeis(
    db_settings: Settings, engine: Engine
) -> None:
    at_revision(db_settings, "0030")

    downgrade_migrations(db_settings, "0029")

    assert "yielded_at" in {c["name"] for c in inspect(engine).get_columns("attempt")}
    assert _convalidated(engine) is not None
