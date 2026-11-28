"""Migração 0032: instante do pedido de parada da Run (#442, 15a).

Expand: só a coluna anulável `run.stop_requested_at`, que o código anterior ignora. O backfill dá
às Runs já em `stopping` o valor que a medição usava (o menor entre o cancelamento e o prazo, ou
`now()`), e rodar de novo não muda nada. O downgrade remove a coluna.
"""

import importlib.util
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import Connection, Engine, inspect, text

from goblinshift.platform.db import make_engine
from goblinshift.platform.settings import Settings
from tests.conftest import downgrade_migrations, run_migrations

pytestmark = pytest.mark.integration

MIGRATION = (
    Path(__file__).resolve().parents[1] / "migrations" / "versions" / "0032_pedido_de_parada.py"
)


@pytest.fixture
def engine(db_settings: Settings) -> Iterator[Engine]:
    engine = make_engine(db_settings)
    yield engine
    engine.dispose()
    run_migrations(db_settings, "head")


def at_0031(settings: Settings) -> None:
    """Leva o schema a 0031 venha ele de baixo (banco novo) ou de cima (head)."""
    run_migrations(settings, "0031")
    downgrade_migrations(settings, "0031")


def run_columns(engine: Engine) -> set[str]:
    return {column["name"] for column in inspect(engine).get_columns("run")}


def new_owner(conn: Connection) -> UUID:
    owner: UUID = conn.execute(
        text(
            "INSERT INTO app_user (issuer, subject, display_name) "
            "VALUES ('teste', gen_random_uuid()::text, 'Ana') RETURNING id"
        )
    ).scalar_one()
    return owner


def new_run(
    conn: Connection,
    status: str,
    *,
    cancel_ago: str | None = None,
    deadline_ago: str | None = None,
) -> UUID:
    """Run inserida como o código anterior grava, sem `stop_requested_at`; uma conta por Run."""
    owner = new_owner(conn)
    run_id: UUID = conn.execute(
        text(
            "INSERT INTO run (owner_id, recipe_id, recipe_version, executor_digest, parameters, "
            "limits, status, phase, stop_reason, queue_expires_at, cancel_requested_at, "
            "deadline_at) "
            "VALUES (:owner, 'detox', 1, 'sha256:teste', '{}', '{}', :status, "
            "CASE WHEN :status = 'running' THEN 'processing' END, "
            "CASE WHEN :status = 'stopping' THEN 'CANCELED' END, now() + interval '2 hours', "
            "now() - CAST(:cancel AS interval), now() - CAST(:deadline AS interval)) "
            "RETURNING id"
        ),
        {"owner": owner, "status": status, "cancel": cancel_ago, "deadline": deadline_ago},
    ).scalar_one()
    return run_id


def runs_left_by_the_old_code(engine: Engine) -> dict[str, UUID]:
    with engine.begin() as conn:
        return {
            "only_deadline": new_run(conn, "stopping", deadline_ago="5 minutes"),
            "both": new_run(conn, "stopping", cancel_ago="20 minutes", deadline_ago="-25 minutes"),
            "neither": new_run(conn, "stopping"),
            "running": new_run(conn, "running", deadline_ago="-25 minutes"),
        }


def stop_instants(engine: Engine, run_ids: dict[str, UUID]) -> dict[str, datetime | None]:
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT id, stop_requested_at FROM run WHERE id = ANY(:ids)"),
            {"ids": list(run_ids.values())},
        ).all()
    by_id = {row.id: row.stop_requested_at for row in rows}
    return {name: by_id[run_id] for name, run_id in run_ids.items()}


def reference_instants(engine: Engine, run_ids: dict[str, UUID]) -> dict[str, datetime | None]:
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT id, cancel_requested_at, deadline_at FROM run WHERE id = ANY(:ids)"),
            {"ids": list(run_ids.values())},
        ).all()
    by_id = {row.id: (row.cancel_requested_at, row.deadline_at) for row in rows}
    return {
        "only_deadline": by_id[run_ids["only_deadline"]][1],
        "both": by_id[run_ids["both"]][0],
    }


def test_0032_adiciona_a_coluna_e_o_downgrade_a_remove(
    db_settings: Settings, engine: Engine
) -> None:
    run_migrations(db_settings, "0032")
    assert "stop_requested_at" in run_columns(engine)

    downgrade_migrations(db_settings, "0031")

    assert "stop_requested_at" not in run_columns(engine)


def test_0032_preenche_as_runs_em_stopping_com_o_instante_da_medicao(
    db_settings: Settings, engine: Engine
) -> None:
    at_0031(db_settings)
    runs = runs_left_by_the_old_code(engine)
    expected = reference_instants(engine, runs)

    run_migrations(db_settings, "0032")

    instants = stop_instants(engine, runs)
    assert instants["only_deadline"] == expected["only_deadline"]
    assert instants["both"] == expected["both"]
    assert instants["neither"] is not None
    assert instants["running"] is None


def backfill_sql() -> str:
    spec = importlib.util.spec_from_file_location("migration_0032", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    sql: str = module.BACKFILL
    return sql


def test_rodar_o_backfill_da_0032_de_novo_nao_muda_o_instante(
    db_settings: Settings, engine: Engine
) -> None:
    at_0031(db_settings)
    runs = runs_left_by_the_old_code(engine)
    run_migrations(db_settings, "0032")
    first = stop_instants(engine, runs)

    with engine.begin() as conn:
        conn.execute(text(backfill_sql()))

    assert stop_instants(engine, runs) == first
