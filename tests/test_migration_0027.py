"""Migração 0027: marca de término reportado da Run, para o `run.finished` sair ao menos uma vez.

Expand: coluna anulável e índice parcial novos, que o código anterior ignora. As Runs que já
terminaram nascem marcadas como reportadas, senão a primeira varredura reemitiria o histórico. O
downgrade remove a coluna e o índice.
"""

from collections.abc import Iterator
from uuid import UUID

import pytest
from sqlalchemy import Connection, Engine, inspect, text

from goblinshift.platform.db import make_engine
from goblinshift.platform.settings import Settings
from tests.conftest import downgrade_migrations, run_migrations

pytestmark = pytest.mark.integration

INDEX = "run_finish_unreported_idx"
EXPECTED_INDEX = (
    "CREATE INDEX run_finish_unreported_idx ON public.run USING btree (finished_at) "
    "WHERE ((finished_at IS NOT NULL) AND (finished_reported_at IS NULL))"
)


@pytest.fixture
def engine(db_settings: Settings) -> Iterator[Engine]:
    engine = make_engine(db_settings)
    yield engine
    engine.dispose()
    run_migrations(db_settings, "head")


def new_run(conn: Connection, *, finished: bool) -> UUID:
    owner = conn.execute(
        text(
            "INSERT INTO app_user (issuer, subject, display_name) "
            "VALUES ('teste', gen_random_uuid()::text, 'Ana') RETURNING id"
        )
    ).scalar_one()
    run_id: UUID = conn.execute(
        text(
            "INSERT INTO run (owner_id, recipe_id, recipe_version, executor_digest, parameters, "
            "limits, status, queue_expires_at, finished_at) VALUES (:owner, 'csv-janitor', 1, "
            "'local-dev', '{}', '{}', CASE WHEN :finished THEN 'succeeded' ELSE 'queued' END, "
            "now() + interval '1 hour', "
            "CASE WHEN :finished THEN now() - interval '3 days' END) RETURNING id"
        ),
        {"owner": owner, "finished": finished},
    ).scalar_one()
    return run_id


def index_state(engine: Engine) -> tuple[str, bool] | None:
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT pg_get_indexdef(i.indexrelid) AS definition, i.indisvalid AS valid "
                "FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid WHERE c.relname = :name"
            ),
            {"name": INDEX},
        ).one_or_none()
    return None if row is None else (row.definition, row.valid)


def test_migracao_marca_as_runs_ja_terminais_como_reportadas(
    db_settings: Settings, engine: Engine
) -> None:
    # Outro teste pode ter deixado o schema na head: subir até 0026 não desce, então desce.
    run_migrations(db_settings, "0026")
    downgrade_migrations(db_settings, "0026")
    with engine.begin() as conn:
        finished = new_run(conn, finished=True)
        queued = new_run(conn, finished=False)

    run_migrations(db_settings, "0027")

    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT id, finished_reported_at = finished_at AS same FROM run "
                "WHERE id IN (:finished, :queued)"
            ),
            {"finished": finished, "queued": queued},
        )
        marks = {row.id: row.same for row in rows}
    assert marks == {finished: True, queued: None}


def test_0027_cria_o_indice_parcial_valido(db_settings: Settings, engine: Engine) -> None:
    run_migrations(db_settings, "0027")

    assert index_state(engine) == (EXPECTED_INDEX, True)


def test_downgrade_da_0027_remove_a_coluna_e_o_indice(
    db_settings: Settings, engine: Engine
) -> None:
    run_migrations(db_settings, "0027")

    downgrade_migrations(db_settings, "0026")

    assert index_state(engine) is None
    assert "finished_reported_at" not in {c["name"] for c in inspect(engine).get_columns("run")}
