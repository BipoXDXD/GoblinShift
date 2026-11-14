"""Migração 0017: `app_user.purge_attempted_at`, o rodízio da purga das contas (#442).

Expand: coluna anulável e sem default, que o código anterior ignora. O downgrade a remove.
"""

from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, inspect
from sqlalchemy.engine.interfaces import ReflectedColumn

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


def purge_attempted_at(engine: Engine) -> ReflectedColumn | None:
    columns = inspect(engine).get_columns("app_user")
    return next((column for column in columns if column["name"] == "purge_attempted_at"), None)


def test_0017_cria_a_tentativa_anulavel_e_sem_default(
    db_settings: Settings, engine: Engine
) -> None:
    run_migrations(db_settings, "0017")

    column = purge_attempted_at(engine)

    assert column is not None
    assert column["nullable"] is True
    assert column["default"] is None


def test_downgrade_da_0017_remove_a_tentativa(db_settings: Settings, engine: Engine) -> None:
    run_migrations(db_settings, "0017")

    downgrade_migrations(db_settings, "0016")

    assert purge_attempted_at(engine) is None
