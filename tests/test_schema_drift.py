"""O banco migrado tem o mesmo DDL que o `metadata` dos `tables.py` (#442, 23d).

Compara o catálogo do PostgreSQL de dois schemas: `public`, criado pelas migrações, e `modelo`,
criado por `metadata.create_all`. Diferente do `compare_metadata`, vê CHECK, predicado de índice
parcial e `ON DELETE` das FKs. As migrações são o que produção tem: um drift se corrige no
`tables.py` ou com migração nova, nunca editando uma migração já aplicada.
"""

from collections.abc import Iterator

import pytest
from sqlalchemy import Engine

# Importar os módulos registra as tabelas no MetaData comum, como em `migrations/env.py`.
import goblinshift.capacity.tables
import goblinshift.files.tables
import goblinshift.identity.tables
import goblinshift.presets.tables
import goblinshift.runs.tables  # noqa: F401
from goblinshift.platform.db import make_engine, metadata
from goblinshift.platform.settings import Settings
from tests.conftest import run_migrations
from tests.schema_snapshot import schema_snapshot

pytestmark = pytest.mark.integration


@pytest.fixture
def engine(db_settings: Settings) -> Iterator[Engine]:
    run_migrations(db_settings, "head")
    engine = make_engine(db_settings)
    yield engine
    engine.dispose()


def test_banco_migrado_tem_o_mesmo_ddl_do_metadata(engine: Engine) -> None:
    with engine.connect() as conn:
        migrado = schema_snapshot(conn, "public")
        conn.exec_driver_sql("CREATE SCHEMA modelo")
        conn.exec_driver_sql("SET LOCAL search_path = modelo")
        metadata.create_all(conn)
        modelo = schema_snapshot(conn, "modelo")
        conn.rollback()

    # Sem isto, dois retratos vazios (schema errado, filtro quebrado) passariam como iguais.
    assert {table for table, _ in migrado.columns} == set(metadata.tables)
    assert modelo.columns == migrado.columns
    assert modelo.constraints == migrado.constraints
    assert modelo.indexes == migrado.indexes
