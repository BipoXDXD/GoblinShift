"""Migração 0028: backfill da marca de término reportado e índice parcial da varredura.

As Runs que já terminaram nascem marcadas como reportadas, senão a primeira varredura reemitiria
o histórico. O índice nasce CONCURRENTLY, fora da transação. A revisão inteira pode ser repetida:
o retry do `lock_timeout` (ADR 057) a refaz quando o CREATE INDEX CONCURRENTLY desiste, com o
backfill já gravado e um índice INVALID deixado para trás.
"""

from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, inspect, text
from sqlalchemy.exc import IntegrityError

from goblinshift.platform.db import make_engine
from goblinshift.platform.settings import Settings
from tests.conftest import downgrade_migrations, run_migrations, stamp_migrations
from tests.run_finish_migration_support import (
    EXPECTED_INDEX,
    INDEX,
    at_revision,
    index_state,
    new_run,
    reported_marks,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def engine(db_settings: Settings) -> Iterator[Engine]:
    engine = make_engine(db_settings)
    yield engine
    engine.dispose()
    run_migrations(db_settings, "head")


def test_0028_marca_as_runs_ja_terminais_como_reportadas(
    db_settings: Settings, engine: Engine
) -> None:
    at_revision(db_settings, "0027")
    with engine.begin() as conn:
        finished = new_run(conn, finished=True)
        queued = new_run(conn, finished=False)

    run_migrations(db_settings, "0028")

    assert reported_marks(engine, finished, queued) == {finished: True, queued: None}


def test_0028_cria_o_indice_parcial_valido(db_settings: Settings, engine: Engine) -> None:
    at_revision(db_settings, "0027")

    run_migrations(db_settings, "0028")

    assert index_state(engine) == (EXPECTED_INDEX, True)


def test_0028_refaz_o_indice_invalido_de_uma_tentativa_anterior(
    db_settings: Settings, engine: Engine
) -> None:
    # A tentativa anterior gravou o backfill e caiu no meio do CREATE INDEX CONCURRENTLY.
    at_revision(db_settings, "0027")
    with engine.begin() as conn:
        finished = new_run(conn, finished=True)
        new_run(conn, finished=True)
        conn.execute(
            text("UPDATE run SET finished_reported_at = finished_at WHERE id = :id"),
            {"id": finished},
        )
    with (
        engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn,
        pytest.raises(IntegrityError),
    ):
        # Duas Runs com a mesma receita: o índice único falha e fica INVALID com o nome dele.
        conn.execute(text(f"CREATE UNIQUE INDEX CONCURRENTLY {INDEX} ON run (recipe_id)"))
    leftover = index_state(engine)
    assert leftover is not None
    assert leftover[1] is False

    run_migrations(db_settings, "0028")

    assert index_state(engine) == (EXPECTED_INDEX, True)
    assert reported_marks(engine, finished) == {finished: True}


def test_0028_repetida_inteira_depois_de_aplicada_termina_igual(
    db_settings: Settings, engine: Engine
) -> None:
    # O carimbo sai por último: o retry roda a 0028 de novo sobre o que ela já gravou.
    at_revision(db_settings, "0027")
    with engine.begin() as conn:
        finished = new_run(conn, finished=True)
    run_migrations(db_settings, "0028")
    stamp_migrations(db_settings, "0027")

    run_migrations(db_settings, "0028")

    assert index_state(engine) == (EXPECTED_INDEX, True)
    assert reported_marks(engine, finished) == {finished: True}


def test_downgrade_da_0028_remove_o_indice_e_mantem_a_coluna(
    db_settings: Settings, engine: Engine
) -> None:
    at_revision(db_settings, "0028")

    downgrade_migrations(db_settings, "0027")

    assert index_state(engine) is None
    assert "finished_reported_at" in {c["name"] for c in inspect(engine).get_columns("run")}
