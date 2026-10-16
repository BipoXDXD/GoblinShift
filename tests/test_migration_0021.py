"""Migração 0021: `account_quota`, o override das cotas diárias por conta (1.7; `set-limits`).

Expand: tabela nova, que o código anterior não lê. CASCADE para a conta, porque a linha é
configuração dela, sem histórico. O downgrade apaga a tabela (os overrides se perdem).
"""

from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, inspect, text
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


def new_user(engine: Engine) -> object:
    with engine.begin() as conn:
        return conn.execute(
            text(
                "INSERT INTO app_user (issuer, subject, display_name) "
                "VALUES ('teste', gen_random_uuid()::text, 'Ana') RETURNING id"
            )
        ).scalar_one()


def test_override_sai_junto_com_a_conta(db_settings: Settings, engine: Engine) -> None:
    run_migrations(db_settings, "0021")
    user_id = new_user(engine)
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO account_quota (owner_id, runs_per_24h) VALUES (:id, 5)"),
            {"id": user_id},
        )
        conn.execute(text("DELETE FROM app_user WHERE id = :id"), {"id": user_id})
        left = conn.execute(text("SELECT count(*) FROM account_quota")).scalar_one()

    assert left == 0


@pytest.mark.parametrize("column", ["runs_per_24h", "input_bytes_per_24h"])
def test_override_zero_e_recusado(db_settings: Settings, engine: Engine, column: str) -> None:
    run_migrations(db_settings, "0021")
    user_id = new_user(engine)

    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(
            text(f"INSERT INTO account_quota (owner_id, {column}) VALUES (:id, 0)"),  # noqa: S608
            {"id": user_id},
        )


def test_downgrade_apaga_a_tabela(db_settings: Settings, engine: Engine) -> None:
    run_migrations(db_settings, "0021")

    downgrade_migrations(db_settings, "0020")

    assert "account_quota" not in inspect(engine).get_table_names()
