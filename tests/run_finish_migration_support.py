"""Apoio dos testes das migrações 0027 e 0028 (marca de término reportado da Run)."""

from uuid import UUID

from sqlalchemy import Connection, Engine, text

from goblinshift.platform.settings import Settings
from tests.conftest import downgrade_migrations, run_migrations

INDEX = "run_finish_unreported_idx"
EXPECTED_INDEX = (
    "CREATE INDEX run_finish_unreported_idx ON public.run USING btree (finished_at) "
    "WHERE ((finished_at IS NOT NULL) AND (finished_reported_at IS NULL))"
)


def at_revision(settings: Settings, revision: str) -> None:
    """Leva o schema até `revision`, de cima ou de baixo: subir a partir da head não desce."""
    run_migrations(settings, revision)
    downgrade_migrations(settings, revision)


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


def reported_marks(engine: Engine, *run_ids: UUID) -> dict[UUID, bool | None]:
    """Por Run: a marca igual ao `finished_at` (True), diferente (False) ou ausente (None)."""
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT id, finished_reported_at = finished_at AS same FROM run "
                "WHERE id = ANY(:ids)"
            ),
            {"ids": list(run_ids)},
        )
        return {row.id: row.same for row in rows}


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
