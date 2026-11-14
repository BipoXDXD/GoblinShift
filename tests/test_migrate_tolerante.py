"""O `migrate` de uma imagem anterior com o banco já à frente dela.

Pela política expand/contract (docs/GoblinShiftPlan.md, 3.8), o código anterior tolera o
schema mais novo, então voltar para a imagem anterior não pode deixar o `migrate` falhando
(`api` e `worker` esperam que ele termine com sucesso). Em produção, o `migrate` só sobe para
o head: descer o schema é restauração de backup, não comando.

Os testes rodam o CLI do Alembic num subprocesso, como o serviço `migrate` faz.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from goblinshift.platform.settings import Settings
from tests.alembic_cli import (
    db_version,
    head_revision,
    previous_revision,
    run_alembic,
    with_password_file,
)
from tests.conftest import downgrade_migrations, run_migrations

# Revisão que nenhuma imagem conhece: a de uma imagem mais nova que esta.
NEWER_REVISION = "ffffffffffff"


def _stamp_raw(settings: Settings, revision: str) -> None:
    engine = create_engine(settings.database_url)
    try:
        with engine.begin() as conn:
            conn.execute(text("UPDATE alembic_version SET version_num = :r"), {"r": revision})
    finally:
        engine.dispose()


@pytest.fixture
def at_head(db_settings: Settings, tmp_path: Path) -> Iterator[Settings]:
    settings = with_password_file(db_settings, tmp_path)
    run_migrations(settings, "head")
    yield settings
    # Outros testes usam o mesmo banco: ele volta ao head. A revisão inventada só existe na
    # tabela de versão, e o schema continua no head.
    if db_version(settings) == NEWER_REVISION:
        _stamp_raw(settings, head_revision())
    run_migrations(settings, "head")


def test_banco_a_frente_da_imagem_avisa_e_sai_com_zero_sem_migrar(at_head: Settings) -> None:
    _stamp_raw(at_head, NEWER_REVISION)

    result = run_alembic(at_head, "upgrade", "head")

    assert result.returncode == 0, result.stderr
    assert "WARN" in result.stderr
    assert NEWER_REVISION in result.stderr
    assert head_revision() in result.stderr
    assert db_version(at_head) == NEWER_REVISION


def test_banco_atras_da_imagem_chega_ao_head(at_head: Settings) -> None:
    downgrade_migrations(at_head, "-1")
    assert db_version(at_head) == previous_revision()

    result = run_alembic(at_head, "upgrade", "head")

    assert result.returncode == 0, result.stderr
    assert db_version(at_head) == head_revision()


def test_erro_real_do_alembic_continua_falhando(at_head: Settings) -> None:
    unreachable = at_head.model_copy(update={"db_name": "banco_que_nao_existe"})

    result = run_alembic(unreachable, "upgrade", "head")

    assert result.returncode != 0
    assert "banco_que_nao_existe" in result.stderr


@pytest.mark.parametrize("target", ["-1", "base"])
def test_downgrade_em_producao_e_recusado_sem_mexer_no_banco(
    at_head: Settings, target: str
) -> None:
    result = run_alembic(at_head, "downgrade", target)

    assert result.returncode != 0
    assert "produção" in result.stderr
    assert db_version(at_head) == head_revision()


def test_downgrade_fora_de_producao_continua_permitido(at_head: Settings) -> None:
    result = run_alembic(at_head, "downgrade", "-1", environment="development")

    assert result.returncode == 0, result.stderr
    assert db_version(at_head) == previous_revision()


@pytest.mark.parametrize("target", ["head", "heads"])
def test_stamp_no_head_em_producao_e_aceito(at_head: Settings, target: str) -> None:
    result = run_alembic(at_head, "stamp", target)

    assert result.returncode == 0, result.stderr
    assert db_version(at_head) == head_revision()


def test_stamp_em_outra_revisao_em_producao_e_recusado(at_head: Settings) -> None:
    result = run_alembic(at_head, "stamp", previous_revision())

    assert result.returncode != 0
    assert "produção" in result.stderr
    assert db_version(at_head) == head_revision()


def test_stamp_head_com_banco_a_frente_avisa_sem_regravar_a_versao(at_head: Settings) -> None:
    _stamp_raw(at_head, NEWER_REVISION)

    result = run_alembic(at_head, "stamp", "head")

    assert result.returncode == 0, result.stderr
    assert NEWER_REVISION in result.stderr
    assert db_version(at_head) == NEWER_REVISION


def test_current_em_producao_continua_permitido(at_head: Settings) -> None:
    result = run_alembic(at_head, "current")

    assert result.returncode == 0, result.stderr
    assert head_revision() in result.stdout
