"""Migração 0033: valida os tetos de reserva criados `NOT VALID` na 0032 (#442, R5)."""

from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, text

from goblinshift.platform.db import make_engine
from goblinshift.platform.settings import Settings
from tests.conftest import downgrade_migrations, run_migrations, stamp_migrations
from tests.run_finish_migration_support import at_revision

pytestmark = pytest.mark.integration

# O controlador renumera no merge (R1 do preflight): os testes citam só estas constantes.
REVISION = "0033"
PREVIOUS = "0032"
# Abaixo da 0032, que cria os tetos: descer só até a 0032 deixaria os já validados pela 0033.
BEFORE_CEILINGS = "0030"
CHECKS = (
    ("capacity_pool", "capacity_pool_max_amount_ceiling_check"),
    ("account_quota", "account_quota_input_bytes_per_24h_ceiling_check"),
)


@pytest.fixture
def engine(db_settings: Settings) -> Iterator[Engine]:
    engine = make_engine(db_settings)
    yield engine
    engine.dispose()
    run_migrations(db_settings, "head")


def validity(engine: Engine) -> list[bool | None]:
    with engine.connect() as conn:
        return [
            conn.execute(
                text(
                    "SELECT convalidated FROM pg_constraint "
                    "WHERE conrelid = CAST(:table AS regclass) AND conname = :name"
                ),
                {"table": table, "name": name},
            ).scalar_one_or_none()
            for table, name in CHECKS
        ]


def test_valida_os_tetos(db_settings: Settings, engine: Engine) -> None:
    at_revision(db_settings, BEFORE_CEILINGS)
    run_migrations(db_settings, PREVIOUS)
    assert validity(engine) == [False, False]

    run_migrations(db_settings, REVISION)

    assert validity(engine) == [True, True]


def test_repetida_depois_de_aplicada_termina_igual(db_settings: Settings, engine: Engine) -> None:
    at_revision(db_settings, REVISION)
    stamp_migrations(db_settings, PREVIOUS)

    run_migrations(db_settings, REVISION)

    assert validity(engine) == [True, True]


def test_downgrade_mantem_os_tetos(db_settings: Settings, engine: Engine) -> None:
    at_revision(db_settings, REVISION)

    downgrade_migrations(db_settings, PREVIOUS)

    assert validity(engine) == [True, True]
