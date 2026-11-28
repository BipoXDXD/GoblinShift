"""Migração 0032: outbox de remoção de arquivos, `file_removal` (#442, 17b).

Expand: tabela nova, que o código anterior não lê nem grava; a réplica anterior segue removendo
depois do commit sem registrar a chave, como antes. A chave é a PK: pedir duas vezes a remoção do
mesmo arquivo é uma linha só. O downgrade apaga a tabela e, com ela, as remoções ainda pendentes:
os arquivos ficam no disco como órfãos, que `gs-admin orphan-files` lista.
"""

from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, inspect, text
from sqlalchemy.exc import IntegrityError

from goblinshift.platform.db import make_engine
from goblinshift.platform.settings import Settings
from tests.conftest import downgrade_migrations, run_migrations

pytestmark = pytest.mark.integration

_ENQUEUE = text("INSERT INTO file_removal (storage_key) VALUES (:key) RETURNING requested_at")


@pytest.fixture
def engine(db_settings: Settings) -> Iterator[Engine]:
    engine = make_engine(db_settings)
    yield engine
    engine.dispose()
    run_migrations(db_settings, "head")


def current_revision(engine: Engine) -> str:
    with engine.connect() as conn:
        revision: str = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    return revision


def test_upgrade_cria_a_outbox_com_a_chave_como_pk(db_settings: Settings, engine: Engine) -> None:
    run_migrations(db_settings, "0032")

    assert inspect(engine).get_pk_constraint("file_removal")["constrained_columns"] == [
        "storage_key"
    ]


def test_pedido_sem_horario_ganha_o_do_banco(db_settings: Settings, engine: Engine) -> None:
    run_migrations(db_settings, "0032")

    with engine.begin() as conn:
        requested_at = conn.execute(_ENQUEUE, {"key": "uploads/m0032-a"}).scalar_one()
        now = conn.execute(text("SELECT now()")).scalar_one()

    assert requested_at == now


def test_mesma_chave_duas_vezes_e_recusada(db_settings: Settings, engine: Engine) -> None:
    run_migrations(db_settings, "0032")
    with engine.begin() as conn:
        conn.execute(_ENQUEUE, {"key": "uploads/m0032-b"})

    with pytest.raises(IntegrityError, match="file_removal_pkey"), engine.begin() as conn:
        conn.execute(_ENQUEUE, {"key": "uploads/m0032-b"})


def test_downgrade_apaga_a_outbox(db_settings: Settings, engine: Engine) -> None:
    run_migrations(db_settings, "0032")
    with engine.begin() as conn:
        conn.execute(_ENQUEUE, {"key": "uploads/m0032-c"})

    downgrade_migrations(db_settings, "0031", allow_lossy=False)

    assert current_revision(engine) == "0031"
    assert not inspect(engine).has_table("file_removal")
