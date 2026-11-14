"""Migração 0019: índices das buscas da manutenção e da FK de `object_use` (#442).

Expand: só índices novos, que o código anterior ignora. O downgrade os remove.
"""

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import Connection, Engine, event, text
from sqlalchemy.orm import Session

from goblinshift.identity.purge import delete_ended_web_sessions
from goblinshift.platform.db import make_engine
from goblinshift.platform.settings import Settings
from tests.conftest import downgrade_migrations, run_migrations

pytestmark = pytest.mark.integration

NOW = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)

EXPECTED = {
    "object_use_object_id_idx": (
        "CREATE INDEX object_use_object_id_idx ON public.object_use USING btree (object_id)"
    ),
    "stored_object_attempt_id_idx": (
        "CREATE INDEX stored_object_attempt_id_idx ON public.stored_object USING btree "
        "(attempt_id) WHERE (attempt_id IS NOT NULL)"
    ),
    "web_session_ended_at_idx": (
        "CREATE INDEX web_session_ended_at_idx ON public.web_session USING btree "
        "(LEAST(revoked_at, idle_expires_at, absolute_expires_at))"
    ),
    "resource_reservation_released_at_idx": (
        "CREATE INDEX resource_reservation_released_at_idx ON public.resource_reservation "
        "USING btree (released_at) WHERE (released_at IS NOT NULL)"
    ),
}


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


def test_0019_cria_os_quatro_indices(db_settings: Settings, engine: Engine) -> None:
    run_migrations(db_settings, "0019")

    assert index_definitions(engine) == EXPECTED


def test_downgrade_da_0019_remove_os_indices(db_settings: Settings, engine: Engine) -> None:
    run_migrations(db_settings, "0019")

    downgrade_migrations(db_settings, "0018")

    assert index_definitions(engine) == {}


def test_limpeza_de_sessoes_usa_o_indice_do_fim(db_settings: Settings, engine: Engine) -> None:
    run_migrations(db_settings, "head")
    captured: list[tuple[str, Any]] = []

    def capture(
        _conn: Connection, _cursor: object, statement: str, parameters: Any, *_: object
    ) -> None:
        captured.append((statement, parameters))

    with engine.connect() as connection:
        # Tabela vazia: sem isto o planejador prefere a varredura sequencial, e o teste não
        # diria se o predicado casa com a expressão do índice.
        connection.exec_driver_sql("SET LOCAL enable_seqscan = off")
        event.listen(connection, "before_cursor_execute", capture)
        with Session(bind=connection) as session:
            delete_ended_web_sessions(session, NOW, 10)
        event.remove(connection, "before_cursor_execute", capture)
        statement, parameters = captured[-1]
        plan = connection.exec_driver_sql(f"EXPLAIN {statement}", parameters).scalars().all()
        connection.rollback()

    assert any("web_session_ended_at_idx" in line for line in plan), plan
