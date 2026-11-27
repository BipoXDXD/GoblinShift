"""Migração 0029: coluna `attempt.yielded_at` e CHECK NOT VALID (#442).

Expand: o código anterior ignora a coluna. A validação do CHECK fica na 0030 (Split Phase,
sem varrer `attempt` sob ACCESS EXCLUSIVE nesta revisão).
"""

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine, inspect, text
from sqlalchemy.exc import IntegrityError

from goblinshift.platform.db import make_engine
from goblinshift.platform.settings import Settings
from tests.conftest import downgrade_migrations, run_migrations
from tests.run_finish_migration_support import at_revision

pytestmark = pytest.mark.integration

CHECK_NAME = "attempt_yielded_after_start_check"
AGORA = datetime(2026, 11, 27, 12, 0, tzinfo=UTC)


@pytest.fixture
def engine(db_settings: Settings) -> Iterator[Engine]:
    engine = make_engine(db_settings)
    yield engine
    engine.dispose()
    run_migrations(db_settings, "head")


def _insert_attempt(engine: Engine, *, started: bool) -> object:
    with engine.begin() as conn:
        owner = conn.execute(
            text(
                "INSERT INTO app_user (issuer, subject, display_name) "
                "VALUES ('teste', gen_random_uuid()::text, 'Ana') RETURNING id"
            )
        ).scalar_one()
        run_id = conn.execute(
            text(
                "INSERT INTO run (owner_id, recipe_id, recipe_version, executor_digest, "
                "parameters, limits, status, phase, queue_expires_at) VALUES (:owner, "
                "'csv-janitor', 1, 'local-dev', '{}', '{}', 'running', 'inspecting', "
                "now() + interval '1 hour') RETURNING id"
            ),
            {"owner": owner},
        ).scalar_one()
        return conn.execute(
            text(
                "INSERT INTO attempt (run_id, number, generation, container_name, "
                "lease_expires_at, executor_started_at) VALUES (:run_id, 1, 1, "
                ":name, now() + interval '1 minute', :started) RETURNING id"
            ),
            {
                "run_id": run_id,
                "name": f"gs-exec-{run_id}",
                "started": AGORA if started else None,
            },
        ).scalar_one()


def test_0029_cria_yielded_at_e_check_not_valid(db_settings: Settings, engine: Engine) -> None:
    at_revision(db_settings, "0028")
    existing = _insert_attempt(engine, started=False)

    run_migrations(db_settings, "0029")

    columns = {c["name"]: c for c in inspect(engine).get_columns("attempt")}
    assert columns["yielded_at"]["nullable"] is True
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT contype, convalidated FROM pg_constraint "
                "WHERE conrelid = 'attempt'::regclass AND conname = :name"
            ),
            {"name": CHECK_NAME},
        ).one()
        existing_yielded = conn.execute(
            text("SELECT yielded_at FROM attempt WHERE id = :id"), {"id": existing}
        ).scalar_one()
    assert (row.contype, row.convalidated) == ("c", False)
    assert existing_yielded is None
    with pytest.raises(IntegrityError, match=CHECK_NAME), engine.begin() as conn:
        conn.execute(
            text("UPDATE attempt SET yielded_at = :now WHERE id = :id"),
            {"now": AGORA, "id": existing},
        )


def test_downgrade_da_0029_remove_check_e_coluna(db_settings: Settings, engine: Engine) -> None:
    at_revision(db_settings, "0029")

    downgrade_migrations(db_settings, "0028")

    assert "yielded_at" not in {c["name"] for c in inspect(engine).get_columns("attempt")}
    with engine.connect() as conn:
        constraint = conn.execute(
            text(
                "SELECT 1 FROM pg_constraint "
                "WHERE conrelid = 'attempt'::regclass AND conname = :name"
            ),
            {"name": CHECK_NAME},
        ).one_or_none()
    assert constraint is None
