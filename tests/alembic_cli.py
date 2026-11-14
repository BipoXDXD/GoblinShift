"""O CLI do Alembic como o serviço `migrate` o chama, para os testes de migrate em subprocesso."""

import os
import subprocess
import sys
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text

from goblinshift.platform.settings import Settings

REPO_ROOT = Path(__file__).resolve().parents[1]
CLI_TIMEOUT_S = 120


def head_revision() -> str:
    head = _script().get_current_head()
    assert head is not None
    return head


def previous_revision() -> str:
    previous = _script().get_revision(head_revision()).down_revision
    assert isinstance(previous, str)
    return previous


def with_password_file(settings: Settings, directory: Path) -> Settings:
    """As configurações com a senha em arquivo, como em produção."""
    password_file = directory / "db_password"
    password_file.write_text(settings.db_password.get_secret_value())
    return settings.model_copy(update={"db_password_file": password_file})


def alembic_command(*args: str) -> list[str]:
    return [sys.executable, "-m", "alembic", *args]


def alembic_env(settings: Settings, environment: str = "production") -> dict[str, str]:
    """As variáveis GS_* do serviço `migrate`, com a senha em arquivo."""
    password_file = settings.db_password_file
    assert password_file is not None
    return {
        "PATH": os.environ["PATH"],
        "GS_ENVIRONMENT": environment,
        "GS_DB_HOST": settings.db_host,
        "GS_DB_PORT": str(settings.db_port),
        "GS_DB_USER": settings.db_user,
        "GS_DB_PASSWORD_FILE": str(password_file),
        "GS_DB_NAME": settings.db_name,
    }


def run_alembic(
    settings: Settings,
    *args: str,
    environment: str = "production",
    timeout_s: float = CLI_TIMEOUT_S,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - CLI do Alembic com argumentos fixos do teste
        alembic_command(*args),
        cwd=REPO_ROOT,
        env=alembic_env(settings, environment),
        capture_output=True,
        text=True,
        timeout=timeout_s,
        check=False,
    )


def db_version(settings: Settings) -> str:
    engine = create_engine(settings.database_url)
    try:
        with engine.connect() as conn:
            version: str = conn.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
            return version
    finally:
        engine.dispose()


def _script() -> ScriptDirectory:
    return ScriptDirectory.from_config(Config(str(REPO_ROOT / "alembic.ini")))
