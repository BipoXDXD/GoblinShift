"""Migração 0015: expand para anular os nomes das saídas aos 30 dias (1.7, "Retenção").

`artifact.file_name` e `run_output.logical_name` passam a aceitar NULL. O nome do artefato só sai
de artefato excluído (o CHECK protege o download, que lê o nome de artefato vivo); o nome lógico
não tem CHECK próprio, porque nada na linha de `run_output` diz se a Run terminou, e o único leitor
dele é a listagem de resultados, que já troca NULL pelo nome genérico. A UNIQUE de
`(run_id, logical_name)` continua com NULLS DISTINCT: depois da anulação todas as saídas de uma Run
ficam sem nome, e NULL ali é ausência, não um nome repetido. O downgrade devolve o NOT NULL
preenchendo o anulado com o nome genérico `<papel>-<id do artefato>`.
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


def new_user(conn: Connection) -> UUID:
    user_id: UUID = conn.execute(
        text(
            "INSERT INTO app_user (issuer, subject, display_name) "
            "VALUES ('teste', gen_random_uuid()::text, 'Ana') RETURNING id"
        )
    ).scalar_one()
    return user_id


def new_run(conn: Connection, owner: UUID) -> UUID:
    run_id: UUID = conn.execute(
        text(
            "INSERT INTO run (owner_id, recipe_id, recipe_version, executor_digest, parameters, "
            "limits, status, queue_expires_at, finished_at) VALUES (:owner, 'csv-janitor', 1, "
            "'local-dev', '{}', '{}', 'succeeded', now(), now()) RETURNING id"
        ),
        {"owner": owner},
    ).scalar_one()
    return run_id


def new_output(
    conn: Connection,
    owner: UUID,
    run_id: UUID,
    position: int,
    *,
    deleted: bool,
    role: str = "output",
) -> UUID:
    object_id = conn.execute(
        text(
            "INSERT INTO stored_object (owner_id, storage_key) "
            "VALUES (:owner, gen_random_uuid()::text) RETURNING id"
        ),
        {"owner": owner},
    ).scalar_one()
    artifact_id: UUID = conn.execute(
        text(
            "INSERT INTO artifact (owner_id, object_id, role, file_name, expires_at, deleted_at) "
            "VALUES (:owner, :object, :role, 'clientes.csv', now(), "
            "CASE WHEN :deleted THEN now() END) RETURNING id"
        ),
        {"owner": owner, "object": object_id, "role": role, "deleted": deleted},
    ).scalar_one()
    conn.execute(
        text(
            "INSERT INTO run_output (run_id, position, owner_id, artifact_id, logical_name) "
            "VALUES (:run, :position, :owner, :artifact, :name)"
        ),
        {
            "run": run_id,
            "position": position,
            "owner": owner,
            "artifact": artifact_id,
            "name": f"clientes-{position}.csv",
        },
    )
    return artifact_id


def anonymize(conn: Connection, artifact_id: UUID) -> None:
    conn.execute(text("UPDATE artifact SET file_name = NULL WHERE id = :id"), {"id": artifact_id})
    conn.execute(
        text("UPDATE run_output SET logical_name = NULL WHERE artifact_id = :id"),
        {"id": artifact_id},
    )


def test_0015_torna_os_nomes_das_saidas_anulaveis(db_settings: Settings, engine: Engine) -> None:
    run_migrations(db_settings, "0015")

    assert nullable(engine, "artifact", "file_name") is True
    assert nullable(engine, "run_output", "logical_name") is True


def test_nome_do_artefato_so_sai_de_artefato_excluido(
    db_settings: Settings, engine: Engine
) -> None:
    run_migrations(db_settings, "0015")
    with engine.begin() as conn:
        owner = new_user(conn)
        run_id = new_run(conn, owner)
        deleted = new_output(conn, owner, run_id, 0, deleted=True)
        live = new_output(conn, owner, run_id, 1, deleted=False)
        anonymize(conn, deleted)

    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(text("UPDATE artifact SET file_name = NULL WHERE id = :id"), {"id": live})


def test_varias_saidas_anuladas_da_mesma_run_convivem(
    db_settings: Settings, engine: Engine
) -> None:
    """NULLS DISTINCT de propósito: NULL é ausência de nome, não um nome repetido."""
    run_migrations(db_settings, "0015")
    with engine.begin() as conn:
        owner = new_user(conn)
        run_id = new_run(conn, owner)
        first = new_output(conn, owner, run_id, 0, deleted=True)
        second = new_output(conn, owner, run_id, 1, deleted=True)
        anonymize(conn, first)
        anonymize(conn, second)

    with engine.connect() as conn:
        names = conn.execute(
            text("SELECT count(*) FROM run_output WHERE run_id = :run AND logical_name IS NULL"),
            {"run": run_id},
        ).scalar_one()
    assert names == 2


def test_nome_logico_repetido_na_mesma_run_continua_recusado(
    db_settings: Settings, engine: Engine
) -> None:
    run_migrations(db_settings, "0015")
    with engine.begin() as conn:
        owner = new_user(conn)
        run_id = new_run(conn, owner)
        new_output(conn, owner, run_id, 0, deleted=False)
        new_output(conn, owner, run_id, 1, deleted=False)

    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE run_output SET logical_name = 'clientes-0.csv' "
                "WHERE run_id = :run AND position = 1"
            ),
            {"run": run_id},
        )


def test_indice_parcial_cobre_so_as_saidas_ainda_com_nome(
    db_settings: Settings, engine: Engine
) -> None:
    run_migrations(db_settings, "0015")
    with engine.connect() as conn:
        definition = conn.execute(
            text("SELECT indexdef FROM pg_indexes WHERE indexname = 'run_output_anonymize_idx'")
        ).scalar_one()

    assert "(run_id) WHERE (logical_name IS NOT NULL)" in definition


def test_downgrade_preenche_o_anulado_com_o_nome_generico(
    db_settings: Settings, engine: Engine
) -> None:
    run_migrations(db_settings, "0015")
    with engine.begin() as conn:
        owner = new_user(conn)
        run_id = new_run(conn, owner)
        report = new_output(conn, owner, run_id, 0, deleted=True, role="report")
        output = new_output(conn, owner, run_id, 1, deleted=True)
        kept = new_output(conn, owner, run_id, 2, deleted=False)
        anonymize(conn, report)
        anonymize(conn, output)

    downgrade_migrations(db_settings, "0014")

    assert nullable(engine, "artifact", "file_name") is False
    assert nullable(engine, "run_output", "logical_name") is False
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT a.id, a.file_name, o.logical_name FROM run_output o "
                "JOIN artifact a ON a.id = o.artifact_id WHERE o.run_id = :run ORDER BY o.position"
            ),
            {"run": run_id},
        ).all()
    assert [tuple(row) for row in rows] == [
        (report, f"report-{report}", f"report-{report}"),
        (output, f"output-{output}", f"output-{output}"),
        (kept, "clientes.csv", "clientes-2.csv"),
    ]
