import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from alembic import command
from alembic.config import Config
from pydantic import SecretStr
from sqlalchemy import Connection
from sqlalchemy.dialects.postgresql import insert
from testcontainers.community.postgres import PostgresContainer

from goblinshift.capacity import GLOBAL_STORAGE_MAX_BYTES, Resource
from goblinshift.capacity.tables import capacity_pool
from goblinshift.platform.settings import Settings
from tests.production_log import canary, listen, production_log  # noqa: F401 (fixtures de log)
from tests.skip_guard import pytest_addoption, pytest_configure  # noqa: F401 (hooks do guard)

POSTGRES_IMAGE = (
    "postgres:18.6-trixie@sha256:1267c7e692de235fda2a50dbcdbcbca4672ea6a0298576a6bd9d07cafc319b32"
)


def pytest_runtest_setup(item: pytest.Item) -> None:
    # `needs_root` sai dos jobs comuns por `-m` e roda no passo com sudo do job Verificações. Se
    # chegar a rodar sem root, falha alto em vez de pular.
    if item.get_closest_marker("needs_root") and not _is_linux_root():
        pytest.fail("teste needs_root fora de root no Linux: rode com sudo e `-m needs_root`")


def _is_linux_root() -> bool:
    return sys.platform == "linux" and os.geteuid() == 0


@pytest.fixture(scope="session")
def postgres() -> Iterator[PostgresContainer]:
    with PostgresContainer(POSTGRES_IMAGE, driver=None) as container:
        yield container


@pytest.fixture
def db_settings(postgres: PostgresContainer) -> Settings:
    return Settings(
        environment="test",
        db_host=postgres.get_container_host_ip(),
        db_port=int(postgres.get_exposed_port(5432)),
        db_user=postgres.username,
        db_password=SecretStr(postgres.password),
        db_name=postgres.dbname,
    )


def restore_capacity_seed(conn: Connection) -> None:
    """Recoloca o pool global da migração 0006, que `TRUNCATE app_user CASCADE` também apaga."""
    conn.execute(
        insert(capacity_pool)
        .values(
            scope="global",
            resource=Resource.STORAGE_BYTES.value,
            max_amount=GLOBAL_STORAGE_MAX_BYTES,
        )
        .on_conflict_do_nothing()
    )


def run_migrations(settings: Settings, target: str) -> None:
    """Roda o Alembic com as mesmas variáveis GS_* que a aplicação usa."""
    with _alembic_env(settings):
        if target == "base":
            command.downgrade(Config("alembic.ini"), "base")
        else:
            command.upgrade(Config("alembic.ini"), target)


def downgrade_migrations(settings: Settings, target: str) -> None:
    """Desce o schema até `target`, como `run_migrations` faz com "base"."""
    with _alembic_env(settings):
        command.downgrade(Config("alembic.ini"), target)


def stamp_migrations(settings: Settings, target: str) -> None:
    """Grava `target` como revisão corrente sem rodar nada, como um carimbo que não saiu."""
    with _alembic_env(settings):
        command.stamp(Config("alembic.ini"), target)


@contextmanager
def _alembic_env(settings: Settings) -> Iterator[None]:
    env = {
        "GS_DB_HOST": settings.db_host,
        "GS_DB_PORT": str(settings.db_port),
        "GS_DB_USER": settings.db_user,
        "GS_DB_PASSWORD": settings.db_password.get_secret_value(),
        "GS_DB_NAME": settings.db_name,
    }
    with pytest.MonkeyPatch.context() as mp:
        for key, value in env.items():
            mp.setenv(key, value)
        yield
