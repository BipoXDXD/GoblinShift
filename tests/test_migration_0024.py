"""Migração 0024: índices parciais `CONCURRENTLY` das buscas da retenção (#442, 23g, 23h e item 12).

Expand: só índices novos, que o código anterior ignora. O downgrade os remove. Os testes de plano
olham também o plano genérico: o psycopg prepara a consulta depois de 5 execuções, e um plano
genérico só usa um índice parcial cujo predicado ele prova sem conhecer os parâmetros.
"""

import re
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Connection, Engine, event, text
from sqlalchemy.orm import Session

from goblinshift.files.retention import delete_old_file_rows, expire_incomplete_uploads
from goblinshift.files.storage import FileStorage
from goblinshift.platform.db import make_engine
from goblinshift.platform.settings import Settings
from goblinshift.runs.retention import HISTORY_REFERENCES
from tests.conftest import downgrade_migrations, run_migrations

pytestmark = pytest.mark.integration

NOW = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)
LIMIT = 10

EXPECTED = {
    "object_use_ended_at_idx": (
        "CREATE INDEX object_use_ended_at_idx ON public.object_use USING btree (ended_at) "
        "WHERE (ended_at IS NOT NULL)"
    ),
    "stored_object_gone_idx": (
        "CREATE INDEX stored_object_gone_idx ON public.stored_object USING btree (id) "
        "WHERE (state = ANY (ARRAY['deleted'::text, 'missing'::text]))"
    ),
    "upload_incomplete_idx": (
        "CREATE INDEX upload_incomplete_idx ON public.upload USING btree (expires_at, id) "
        "WHERE (state <> 'ready'::text)"
    ),
}

PLANS = [False, True]
PLAN_IDS = ["plano-custom", "plano-generico"]
PYFORMAT_PARAMETER = re.compile(r"%\((\w+)\)s")


@pytest.fixture
def engine(db_settings: Settings) -> Iterator[Engine]:
    engine = make_engine(db_settings)
    yield engine
    engine.dispose()
    run_migrations(db_settings, "head")


def index_definitions(engine: Engine) -> dict[str, str]:
    with engine.connect() as connection:
        rows = connection.execute(
            text("SELECT indexname, indexdef FROM pg_indexes WHERE indexname = ANY(:names)"),
            {"names": list(EXPECTED)},
        ).all()
    return {row.indexname: row.indexdef for row in rows}


def invalid_indexes(engine: Engine) -> list[str]:
    with engine.connect() as connection:
        return list(
            connection.execute(
                text(
                    "SELECT c.relname FROM pg_index AS x "
                    "JOIN pg_class AS c ON c.oid = x.indexrelid WHERE NOT x.indisvalid"
                )
            ).scalars()
        )


def generic_plan_sql(statement: str) -> str:
    """Troca cada `%(nome)s` por `$n` (um número por nome), para o `EXPLAIN (GENERIC_PLAN)`."""
    numbers: dict[str, int] = {}

    def number(match: re.Match[str]) -> str:
        return f"${numbers.setdefault(match.group(1), len(numbers) + 1)}"

    return PYFORMAT_PARAMETER.sub(number, statement).replace("%%", "%")


def plans_of(engine: Engine, call: Callable[[Session], object], generic: bool) -> dict[str, str]:
    """O plano de cada instrução que `call` executa, indexado pelo SQL; nada fica gravado."""
    captured: list[tuple[str, Any]] = []

    def capture(
        _conn: Connection, _cursor: object, statement: str, parameters: Any, *_: object
    ) -> None:
        captured.append((statement, parameters))

    plans: dict[str, str] = {}
    with engine.connect() as connection:
        # Tabelas vazias: sem isto o planejador prefere a varredura sequencial, e o teste não
        # diria se o predicado casa com o do índice parcial.
        connection.exec_driver_sql("SET LOCAL enable_seqscan = off")
        event.listen(connection, "before_cursor_execute", capture)
        with Session(bind=connection) as session:
            call(session)
        event.remove(connection, "before_cursor_execute", capture)
        for statement, parameters in captured:
            if generic:
                sql = f"EXPLAIN (GENERIC_PLAN) {generic_plan_sql(statement)}"
                lines = connection.exec_driver_sql(sql).scalars().all()
            else:
                lines = (
                    connection.exec_driver_sql(f"EXPLAIN {statement}", parameters).scalars().all()
                )
            plans[statement] = "\n".join(lines)
        connection.rollback()
    return plans


def plan_mentioning(plans: dict[str, str], fragment: str) -> str:
    matching = [plan for statement, plan in plans.items() if fragment in statement]
    assert len(matching) == 1, list(plans)
    return matching[0]


def old_file_rows(session: Session) -> object:
    return delete_old_file_rows(session, NOW, LIMIT, HISTORY_REFERENCES)


def test_0024_cria_os_tres_indices_validos(db_settings: Settings, engine: Engine) -> None:
    run_migrations(db_settings, "0024")

    assert index_definitions(engine) == EXPECTED
    assert invalid_indexes(engine) == []


def test_downgrade_da_0024_remove_os_indices(db_settings: Settings, engine: Engine) -> None:
    run_migrations(db_settings, "0024")

    downgrade_migrations(db_settings, "0023")

    assert index_definitions(engine) == {}


@pytest.mark.parametrize("generic", PLANS, ids=PLAN_IDS)
def test_limpeza_de_usos_encerrados_usa_o_indice_do_fim(
    db_settings: Settings, engine: Engine, generic: bool
) -> None:
    run_migrations(db_settings, "head")

    plan = plan_mentioning(plans_of(engine, old_file_rows, generic), "object_use.ended_at")

    assert "object_use_ended_at_idx" in plan, plan
    assert "Seq Scan on object_use" not in plan, plan


@pytest.mark.parametrize("generic", PLANS, ids=PLAN_IDS)
def test_purga_de_linhas_de_objeto_usa_o_indice_dos_removidos(
    db_settings: Settings, engine: Engine, generic: bool
) -> None:
    run_migrations(db_settings, "head")

    plan = plan_mentioning(plans_of(engine, old_file_rows, generic), "stored_object.created_at")

    assert "stored_object_gone_idx" in plan, plan
    assert "Seq Scan on stored_object" not in plan, plan


@pytest.mark.parametrize("generic", PLANS, ids=PLAN_IDS)
def test_expiracao_de_incompletos_usa_o_indice_dos_incompletos(
    db_settings: Settings, engine: Engine, tmp_path: Path, generic: bool
) -> None:
    run_migrations(db_settings, "head")
    storage = FileStorage(tmp_path / "storage")

    plans = plans_of(engine, lambda s: expire_incomplete_uploads(s, storage), generic)
    plan = plan_mentioning(plans, "upload.expires_at")

    assert "upload_incomplete_idx" in plan, plan
    assert "Seq Scan on upload" not in plan, plan
