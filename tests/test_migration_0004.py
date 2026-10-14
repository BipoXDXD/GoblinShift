"""A migração 0004 reconcilia bancos criados por uma versão anterior da 0002.

A 0002 ganhou `artifact.file_name` depois de já ter sido aplicada na VPS, e o Alembic não
reaplica uma revisão registrada: em produção a coluna faltava e toda publicação falhava com
`UndefinedColumn`. A 0004 acrescenta o que falta e não faz nada num banco que já o tem.
"""

from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, inspect, text

from goblinshift.platform.db import make_engine
from goblinshift.platform.settings import Settings
from tests.conftest import run_migrations

FILE_NAME_CHECK = "artifact_file_name_check"


@pytest.fixture
def engine(db_settings: Settings) -> Iterator[Engine]:
    engine = make_engine(db_settings)
    yield engine
    engine.dispose()


def _artifact_columns(engine: Engine) -> set[str]:
    with engine.connect() as connection:
        return {column["name"] for column in inspect(connection).get_columns("artifact")}


def _artifact_checks(engine: Engine) -> set[str | None]:
    with engine.connect() as connection:
        return {check["name"] for check in inspect(connection).get_check_constraints("artifact")}


def _simulate_old_0002(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(text(f"ALTER TABLE artifact DROP CONSTRAINT {FILE_NAME_CHECK}"))
        connection.execute(text("ALTER TABLE artifact DROP COLUMN file_name"))


def test_banco_da_0002_antiga_ganha_file_name_e_seu_check(
    db_settings: Settings, engine: Engine
) -> None:
    run_migrations(db_settings, "base")
    run_migrations(db_settings, "0003")
    _simulate_old_0002(engine)

    run_migrations(db_settings, "head")

    assert "file_name" in _artifact_columns(engine)
    assert FILE_NAME_CHECK in _artifact_checks(engine)


def test_banco_ja_completo_atravessa_a_0004_sem_mudanca(
    db_settings: Settings, engine: Engine
) -> None:
    run_migrations(db_settings, "base")
    run_migrations(db_settings, "0003")

    run_migrations(db_settings, "head")

    assert "file_name" in _artifact_columns(engine)
    assert FILE_NAME_CHECK in _artifact_checks(engine)
