"""Cada revisão sobe, desce e sobe de novo sem mudar o schema (#442, 23e).

Ir de base a head e voltar só confere as pontas: um `downgrade` que esqueça um CHECK, índice ou
coluna numa tabela que continua existindo passa. Aqui cada revisão é desfeita logo depois de
aplicada, e o retrato do catálogo (`schema_snapshot`) tem de voltar ao da revisão anterior; o
upgrade repetido tem de recriar o mesmo schema da primeira vez.
"""

from collections.abc import Iterator

import pytest
from alembic.config import Config
from alembic.script import Script, ScriptDirectory
from sqlalchemy import Engine

from goblinshift.platform.settings import Settings
from tests.conftest import downgrade_migrations, run_migrations
from tests.schema_snapshot import SchemaSnapshot, schema_snapshot

pytestmark = pytest.mark.integration


@pytest.fixture
def volta_ao_head(db_settings: Settings) -> Iterator[None]:
    yield
    run_migrations(db_settings, "head")


def _revisoes_em_ordem() -> list[Script]:
    return list(reversed(list(ScriptDirectory.from_config(Config("alembic.ini")).walk_revisions())))


def _anterior(revisao: Script) -> str:
    down = revisao.down_revision
    assert down is None or isinstance(down, str), f"{revisao.revision} é merge"
    return down or "base"


def _retrato(engine: Engine) -> SchemaSnapshot:
    with engine.connect() as conn:
        retrato = schema_snapshot(conn, "public")
        conn.rollback()
    return retrato


@pytest.mark.usefixtures("volta_ao_head")
def test_cada_revisao_sobe_desce_e_sobe_sem_mudar_o_schema(
    db_settings: Settings, engine: Engine
) -> None:
    run_migrations(db_settings, "base")
    antes = _retrato(engine)
    for revisao in _revisoes_em_ordem():
        run_migrations(db_settings, revisao.revision)
        depois = _retrato(engine)
        downgrade_migrations(db_settings, _anterior(revisao))
        assert _retrato(engine) == antes, f"downgrade da {revisao.revision}"
        run_migrations(db_settings, revisao.revision)
        assert _retrato(engine) == depois, f"upgrade repetido da {revisao.revision}"
        antes = depois

    # Termina no head, com tabelas: o laço rodou e não comparou só retratos vazios.
    assert antes.columns
