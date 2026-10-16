"""Migração 0009: `audit_event.request_id`, o código que a tela de login recusado mostra (J-10).

Expand: coluna nova e anulável; o código anterior não a grava e continua funcionando. O downgrade
apaga a coluna (os eventos ficam, sem o código).
"""

from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, inspect

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


def audit_columns(engine: Engine) -> dict[str, str]:
    return {
        column["name"]: str(column["type"]) for column in inspect(engine).get_columns("audit_event")
    }


def test_0009_cria_request_id_anulavel(db_settings: Settings, engine: Engine) -> None:
    run_migrations(db_settings, "0009")

    columns = {column["name"]: column for column in inspect(engine).get_columns("audit_event")}

    assert str(columns["request_id"]["type"]) == "UUID"
    assert columns["request_id"]["nullable"] is True


def test_downgrade_tira_a_coluna(db_settings: Settings, engine: Engine) -> None:
    run_migrations(db_settings, "0009")

    downgrade_migrations(db_settings, "0008")

    assert "request_id" not in audit_columns(engine)
