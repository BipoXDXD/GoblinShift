"""Migração 0020: expand para a anulação de 30 dias (docs/GoblinShiftPlan.md, 1.7, "Retenção").

`upload.original_name` e `run.parameters` passam a aceitar NULL, mas só depois do fim: o nome só
sai de upload excluído, e os parâmetros só de Run terminal (os CHECKs protegem quem ainda os lê,
como o claim e a preparação da área de trabalho). Índices parciais cobrem só o que ainda falta
anular ou apagar. O downgrade devolve o NOT NULL preenchendo o que já foi anulado.
"""

from collections.abc import Iterator
from uuid import UUID

import pytest
from sqlalchemy import Connection, Engine, inspect, text
from sqlalchemy.exc import IntegrityError

from goblinshift.platform.db import make_engine
from goblinshift.platform.settings import Settings
from tests.conftest import downgrade_migrations, run_migrations

pytestmark = pytest.mark.integration


@pytest.fixture
def engine(db_settings: Settings) -> Iterator[Engine]:
    engine = make_engine(db_settings)
    yield engine
    engine.dispose()
    run_migrations(db_settings, "head")


def nullable(engine: Engine, table: str, column: str) -> bool:
    columns = {c["name"]: c for c in inspect(engine).get_columns(table)}
    return bool(columns[column]["nullable"])


def index_predicates(engine: Engine, table: str) -> dict[str, str]:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT indexname, indexdef FROM pg_indexes "
                "WHERE schemaname = 'public' AND tablename = :table"
            ),
            {"table": table},
        ).all()
    return {row.indexname: row.indexdef for row in rows}


def new_user(conn: Connection) -> UUID:
    user_id: UUID = conn.execute(
        text(
            "INSERT INTO app_user (issuer, subject, display_name) "
            "VALUES ('teste', gen_random_uuid()::text, 'Ana') RETURNING id"
        )
    ).scalar_one()
    return user_id


def new_upload(conn: Connection, owner: UUID, *, deleted: bool) -> UUID:
    object_id = conn.execute(
        text(
            "INSERT INTO stored_object (owner_id, storage_key) "
            "VALUES (:owner, gen_random_uuid()::text) RETURNING id"
        ),
        {"owner": owner},
    ).scalar_one()
    upload_id: UUID = conn.execute(
        text(
            "INSERT INTO upload (owner_id, object_id, original_name, expected_size, expires_at, "
            "deleted_at) VALUES (:owner, :object, 'fotos.zip', 1, now(), "
            "CASE WHEN :deleted THEN now() END) RETURNING id"
        ),
        {"owner": owner, "object": object_id, "deleted": deleted},
    ).scalar_one()
    return upload_id


def new_run(conn: Connection, owner: UUID, *, finished: bool) -> UUID:
    run_id: UUID = conn.execute(
        text(
            "INSERT INTO run (owner_id, recipe_id, recipe_version, executor_digest, parameters, "
            "limits, status, queue_expires_at, finished_at) VALUES (:owner, 'csv-janitor', 1, "
            "'local-dev', '{\"filters\": []}', '{}', :status, now(), "
            "CASE WHEN :finished THEN now() END) RETURNING id"
        ),
        {"owner": owner, "status": "succeeded" if finished else "queued", "finished": finished},
    ).scalar_one()
    return run_id


def test_0020_torna_nome_e_parametros_anulaveis(db_settings: Settings, engine: Engine) -> None:
    run_migrations(db_settings, "0020")

    assert nullable(engine, "upload", "original_name") is True
    assert nullable(engine, "run", "parameters") is True


def test_nome_anulado_so_em_upload_excluido(db_settings: Settings, engine: Engine) -> None:
    run_migrations(db_settings, "0020")
    with engine.begin() as conn:
        owner = new_user(conn)
        deleted = new_upload(conn, owner, deleted=True)
        live = new_upload(conn, owner, deleted=False)
        conn.execute(text("UPDATE upload SET original_name = NULL WHERE id = :id"), {"id": deleted})

    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(text("UPDATE upload SET original_name = NULL WHERE id = :id"), {"id": live})


def test_parametros_anulados_so_em_run_terminal(db_settings: Settings, engine: Engine) -> None:
    run_migrations(db_settings, "0020")
    with engine.begin() as conn:
        owner = new_user(conn)
        finished = new_run(conn, owner, finished=True)
        conn.execute(text("UPDATE run SET parameters = NULL WHERE id = :id"), {"id": finished})
        conn.execute(
            text(
                "UPDATE run SET status = 'canceled', error_code = 'CANCELED', "
                "finished_at = now() WHERE status = 'queued'"
            )
        )
        queued = new_run(conn, owner, finished=False)

    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(text("UPDATE run SET parameters = NULL WHERE id = :id"), {"id": queued})


def test_indices_parciais_cobrem_so_o_que_falta(db_settings: Settings, engine: Engine) -> None:
    run_migrations(db_settings, "0020")

    runs = index_predicates(engine, "run")
    uploads = index_predicates(engine, "upload")

    assert (
        "WHERE ((parameters IS NOT NULL) AND (finished_at IS NOT NULL))"
        in (runs["run_anonymize_idx"])
    )
    assert "(finished_at) WHERE (finished_at IS NOT NULL)" in runs["run_finished_at_idx"]
    assert (
        "WHERE ((original_name IS NOT NULL) AND (deleted_at IS NOT NULL))"
        in (uploads["upload_anonymize_idx"])
    )


def test_downgrade_preenche_o_anulado_e_volta_o_not_null(
    db_settings: Settings, engine: Engine
) -> None:
    run_migrations(db_settings, "0020")
    with engine.begin() as conn:
        owner = new_user(conn)
        upload_id = new_upload(conn, owner, deleted=True)
        run_id = new_run(conn, owner, finished=True)
        conn.execute(
            text("UPDATE upload SET original_name = NULL WHERE id = :id"), {"id": upload_id}
        )
        conn.execute(text("UPDATE run SET parameters = NULL WHERE id = :id"), {"id": run_id})

    downgrade_migrations(db_settings, "0009")

    assert nullable(engine, "upload", "original_name") is False
    assert nullable(engine, "run", "parameters") is False
    assert "run_anonymize_idx" not in index_predicates(engine, "run")
    with engine.connect() as conn:
        name = conn.execute(
            text("SELECT original_name FROM upload WHERE id = :id"), {"id": upload_id}
        ).scalar_one()
        parameters = conn.execute(
            text("SELECT parameters FROM run WHERE id = :id"), {"id": run_id}
        ).scalar_one()
    assert name == "anonimizado"
    assert parameters == {}
