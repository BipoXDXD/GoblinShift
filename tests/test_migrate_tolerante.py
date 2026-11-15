"""O `migrate` de uma imagem anterior com o banco já à frente dela.

Pela política expand/contract (docs/GoblinShiftPlan.md, 3.8), o código anterior tolera o
schema mais novo, então voltar para a imagem anterior não pode deixar o `migrate` falhando
(`api` e `worker` esperam que ele termine com sucesso). Em produção, o `migrate` só sobe para
o head: descer o schema é restauração de backup, não comando.

Os testes rodam o CLI do Alembic num subprocesso, como o serviço `migrate` faz.
"""

import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text

from goblinshift.platform.settings import Settings
from tests.conftest import downgrade_migrations, run_migrations

REPO_ROOT = Path(__file__).resolve().parents[1]
# Revisão que nenhuma imagem conhece: a de uma imagem mais nova que esta.
NEWER_REVISION = "ffffffffffff"


def _script() -> ScriptDirectory:
    return ScriptDirectory.from_config(Config(str(REPO_ROOT / "alembic.ini")))


def _head() -> str:
    head = _script().get_current_head()
    assert head is not None
    return head


def _previous() -> str:
    previous = _script().get_revision(_head()).down_revision
    assert isinstance(previous, str)
    return previous


def _alembic(
    settings: Settings, *args: str, environment: str = "production"
) -> subprocess.CompletedProcess[str]:
    """O CLI do Alembic como o serviço `migrate` o chama, com a senha em arquivo (produção)."""
    password_file = settings.db_password_file
    assert password_file is not None
    env = {
        "PATH": os.environ["PATH"],
        "GS_ENVIRONMENT": environment,
        "GS_DB_HOST": settings.db_host,
        "GS_DB_PORT": str(settings.db_port),
        "GS_DB_USER": settings.db_user,
        "GS_DB_PASSWORD_FILE": str(password_file),
        "GS_DB_NAME": settings.db_name,
    }
    return subprocess.run(  # noqa: S603 - CLI do Alembic com argumentos fixos do teste
        [sys.executable, "-m", "alembic", *args],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def _db_version(settings: Settings) -> str:
    engine = create_engine(settings.database_url)
    try:
        with engine.connect() as conn:
            revision = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            assert isinstance(revision, str)
            return revision
    finally:
        engine.dispose()


def _stamp_raw(settings: Settings, revision: str) -> None:
    engine = create_engine(settings.database_url)
    try:
        with engine.begin() as conn:
            conn.execute(text("UPDATE alembic_version SET version_num = :r"), {"r": revision})
    finally:
        engine.dispose()


@pytest.fixture
def at_head(db_settings: Settings, tmp_path: Path) -> Iterator[Settings]:
    password_file = tmp_path / "db_password"
    password_file.write_text(db_settings.db_password.get_secret_value())
    settings = db_settings.model_copy(update={"db_password_file": password_file})
    run_migrations(settings, "head")
    yield settings
    # Outros testes usam o mesmo banco: ele volta ao head. A revisão inventada só existe na
    # tabela de versão, e o schema continua no head.
    if _db_version(settings) == NEWER_REVISION:
        _stamp_raw(settings, _head())
    run_migrations(settings, "head")


def test_banco_a_frente_da_imagem_avisa_e_sai_com_zero_sem_migrar(at_head: Settings) -> None:
    _stamp_raw(at_head, NEWER_REVISION)

    result = _alembic(at_head, "upgrade", "head")

    assert result.returncode == 0, result.stderr
    assert "WARN" in result.stderr
    assert NEWER_REVISION in result.stderr
    assert _head() in result.stderr
    assert _db_version(at_head) == NEWER_REVISION


def test_banco_atras_da_imagem_chega_ao_head(at_head: Settings) -> None:
    downgrade_migrations(at_head, "-1")
    assert _db_version(at_head) == _previous()

    result = _alembic(at_head, "upgrade", "head")

    assert result.returncode == 0, result.stderr
    assert _db_version(at_head) == _head()


def test_erro_real_do_alembic_continua_falhando(at_head: Settings) -> None:
    unreachable = at_head.model_copy(update={"db_name": "banco_que_nao_existe"})

    result = _alembic(unreachable, "upgrade", "head")

    assert result.returncode != 0
    assert "banco_que_nao_existe" in result.stderr


@pytest.mark.parametrize("target", ["-1", "base", "head"])
def test_downgrade_em_producao_e_recusado_sem_mexer_no_banco(
    at_head: Settings, target: str
) -> None:
    result = _alembic(at_head, "downgrade", target)

    assert result.returncode != 0
    assert "produção" in result.stderr
    assert _db_version(at_head) == _head()


def test_downgrade_sql_em_producao_e_recusado_sem_emitir_ddl(at_head: Settings) -> None:
    result = _alembic(at_head, "downgrade", f"{_head()}:{_previous()}", "--sql")

    assert result.returncode != 0
    assert "produção" in result.stderr
    assert "DROP INDEX" not in result.stdout
    assert _db_version(at_head) == _head()


def test_current_com_revisao_desconhecida_conserva_o_erro(at_head: Settings) -> None:
    _stamp_raw(at_head, NEWER_REVISION)

    result = _alembic(at_head, "current")

    assert result.returncode != 0
    assert NEWER_REVISION in result.stderr


def test_downgrade_fora_de_producao_continua_permitido(at_head: Settings) -> None:
    result = _alembic(at_head, "downgrade", "-1", environment="development")

    assert result.returncode == 0, result.stderr
    assert _db_version(at_head) == _previous()


@pytest.mark.parametrize("target", ["head", "heads"])
def test_stamp_no_head_em_producao_e_aceito(at_head: Settings, target: str) -> None:
    result = _alembic(at_head, "stamp", target)

    assert result.returncode == 0, result.stderr
    assert _db_version(at_head) == _head()


def test_stamp_em_outra_revisao_em_producao_e_recusado(at_head: Settings) -> None:
    result = _alembic(at_head, "stamp", _previous())

    assert result.returncode != 0
    assert "produção" in result.stderr
    assert _db_version(at_head) == _head()


def test_stamp_head_com_banco_a_frente_avisa_sem_regravar_a_versao(at_head: Settings) -> None:
    _stamp_raw(at_head, NEWER_REVISION)

    result = _alembic(at_head, "stamp", "head")

    assert result.returncode == 0, result.stderr
    assert NEWER_REVISION in result.stderr
    assert _db_version(at_head) == NEWER_REVISION


def test_current_em_producao_continua_permitido(at_head: Settings) -> None:
    result = _alembic(at_head, "current")

    assert result.returncode == 0, result.stderr
    assert _head() in result.stdout
