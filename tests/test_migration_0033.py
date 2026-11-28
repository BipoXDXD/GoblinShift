"""Migração 0033: coluna anulável `run.preset_revision` (#442).

Expand: o código anterior ignora a coluna, e as Runs antigas ficam com revisão nula.
"""

from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, inspect, text

from goblinshift.platform.db import make_engine
from goblinshift.platform.settings import Settings
from tests.conftest import downgrade_migrations, run_migrations
from tests.run_finish_migration_support import at_revision

pytestmark = pytest.mark.integration


@pytest.fixture
def engine(db_settings: Settings) -> Iterator[Engine]:
    engine = make_engine(db_settings)
    yield engine
    engine.dispose()
    run_migrations(db_settings, "head")


def _insert_run(engine: Engine) -> object:
    with engine.begin() as conn:
        owner = conn.execute(
            text(
                "INSERT INTO app_user (issuer, subject, display_name) "
                "VALUES ('teste', gen_random_uuid()::text, 'Ana') RETURNING id"
            )
        ).scalar_one()
        return conn.execute(
            text(
                "INSERT INTO run (owner_id, recipe_id, recipe_version, executor_digest, "
                "parameters, limits, status, queue_expires_at) VALUES (:owner, "
                "'csv-janitor', 1, 'local-dev', '{}', '{}', 'queued', "
                "now() + interval '1 hour') RETURNING id"
            ),
            {"owner": owner},
        ).scalar_one()


def test_0033_acrescenta_coluna_nula_e_downgrade_remove(
    db_settings: Settings, engine: Engine
) -> None:
    at_revision(db_settings, "0031")
    existing = _insert_run(engine)

    run_migrations(db_settings, "0033")

    columns = {c["name"]: c for c in inspect(engine).get_columns("run")}
    assert columns["preset_revision"]["nullable"] is True
    assert columns["preset_revision"]["default"] is None
    with engine.connect() as conn:
        revision = conn.execute(
            text("SELECT preset_revision FROM run WHERE id = :id"), {"id": existing}
        ).scalar_one()
    assert revision is None

    downgrade_migrations(db_settings, "0031")

    assert "preset_revision" not in {c["name"] for c in inspect(engine).get_columns("run")}
