"""Migração 0027: só a coluna `run.finished_reported_at`, anulável e sem default.

Expand: o código anterior a ignora. O backfill e o índice ficam na 0028, que pode ser repetida;
a 0027 roda numa transação curta, e o retry do `lock_timeout` (ADR 057) a refaz do zero.
"""

from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, inspect

from goblinshift.platform.db import make_engine
from goblinshift.platform.settings import Settings
from tests.conftest import downgrade_migrations, run_migrations
from tests.run_finish_migration_support import at_revision, index_state, new_run, reported_marks

pytestmark = pytest.mark.integration


@pytest.fixture
def engine(db_settings: Settings) -> Iterator[Engine]:
    engine = make_engine(db_settings)
    yield engine
    engine.dispose()
    run_migrations(db_settings, "head")


def test_0027_cria_a_coluna_sem_backfill_nem_indice(db_settings: Settings, engine: Engine) -> None:
    at_revision(db_settings, "0026")
    with engine.begin() as conn:
        finished = new_run(conn, finished=True)

    run_migrations(db_settings, "0027")

    columns = {c["name"]: c for c in inspect(engine).get_columns("run")}
    assert columns["finished_reported_at"]["nullable"] is True
    assert reported_marks(engine, finished) == {finished: None}
    assert index_state(engine) is None


def test_downgrade_da_0027_remove_a_coluna(db_settings: Settings, engine: Engine) -> None:
    at_revision(db_settings, "0027")

    downgrade_migrations(db_settings, "0026")

    assert "finished_reported_at" not in {c["name"] for c in inspect(engine).get_columns("run")}
