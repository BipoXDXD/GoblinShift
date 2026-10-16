"""Constraints de `preset` e do vínculo `run.preset_id` no PostgreSQL (3.3)."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import Connection, Engine, Table, delete, insert, select
from sqlalchemy.exc import IntegrityError

from goblinshift.identity.tables import app_user
from goblinshift.platform.db import make_engine
from goblinshift.platform.settings import Settings
from goblinshift.presets.tables import preset
from goblinshift.runs.tables import run
from tests.conftest import run_migrations

pytestmark = pytest.mark.integration

LATER = datetime.now(UTC) + timedelta(hours=1)


@pytest.fixture
def conn(db_settings: Settings) -> Iterator[Connection]:
    run_migrations(db_settings, "head")
    engine: Engine = make_engine(db_settings)
    with engine.connect() as connection:
        transaction = connection.begin()
        yield connection
        transaction.rollback()
    engine.dispose()


@contextmanager
def refused_by(conn: Connection, constraint: str) -> Iterator[None]:
    with pytest.raises(IntegrityError, match=constraint), conn.begin_nested():
        yield


def insert_id(conn: Connection, table: Table, **values: object) -> UUID:
    new_id: UUID = conn.execute(insert(table).values(**values).returning(table.c.id)).scalar_one()
    return new_id


def account(conn: Connection, subject: str) -> UUID:
    return insert_id(conn, app_user, issuer="admin", subject=subject, display_name=subject)


def new_preset(conn: Connection, owner_id: UUID, name: str = "Mensal", **values: object) -> UUID:
    row = {
        "owner_id": owner_id,
        "name": name,
        "recipe_id": "csv-janitor",
        "recipe_version": 1,
        "parameters": {},
    }
    return insert_id(conn, preset, **{**row, **values})


def new_run(conn: Connection, owner_id: UUID, **values: object) -> UUID:
    row = {
        "owner_id": owner_id,
        "recipe_id": "csv-janitor",
        "recipe_version": 1,
        "executor_digest": "sha256:" + "0" * 64,
        "parameters": {},
        "limits": {},
        "status": "succeeded",
        "finished_at": LATER,
        "queue_expires_at": LATER,
    }
    return insert_id(conn, run, **{**row, **values})


def test_run_aponta_para_preset_da_mesma_conta(conn: Connection) -> None:
    owner = account(conn, "alice")
    preset_id = new_preset(conn, owner)

    run_id = new_run(conn, owner, preset_id=preset_id)

    assert conn.execute(select(run.c.preset_id).where(run.c.id == run_id)).scalar_one() == preset_id


def test_run_nao_aponta_para_preset_de_outra_conta(conn: Connection) -> None:
    alice = account(conn, "alice")
    other_preset = new_preset(conn, account(conn, "bob"))

    with refused_by(conn, "run_owner_id_preset_id_fkey"):
        new_run(conn, alice, preset_id=other_preset)


def test_excluir_preset_anula_so_o_vinculo_da_run(conn: Connection) -> None:
    owner = account(conn, "alice")
    preset_id = new_preset(conn, owner)
    run_id = new_run(conn, owner, preset_id=preset_id)

    conn.execute(delete(preset).where(preset.c.id == preset_id))

    row = conn.execute(select(run.c.owner_id, run.c.preset_id).where(run.c.id == run_id)).one()
    assert (row.owner_id, row.preset_id) == (owner, None)


def test_nome_repetido_na_conta_e_recusado(conn: Connection) -> None:
    owner = account(conn, "alice")
    new_preset(conn, owner, "Mensal")

    with refused_by(conn, "preset_owner_id_name_key"):
        new_preset(conn, owner, "Mensal")


@pytest.mark.parametrize("name", ["", "a" * 101], ids=["vazio", "101"])
def test_nome_fora_de_1_a_100_caracteres_e_recusado(conn: Connection, name: str) -> None:
    owner = account(conn, "alice")

    with refused_by(conn, "preset_name_check"):
        new_preset(conn, owner, name)


def test_revisao_zero_e_recusada(conn: Connection) -> None:
    owner = account(conn, "alice")

    with refused_by(conn, "preset_revision_check"):
        new_preset(conn, owner, revision=0)


def test_excluir_a_conta_leva_os_presets(conn: Connection) -> None:
    owner = account(conn, "alice")
    new_preset(conn, owner)

    conn.execute(delete(app_user).where(app_user.c.id == owner))

    assert conn.execute(select(preset.c.id).where(preset.c.owner_id == owner)).all() == []
