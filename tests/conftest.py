import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from alembic import command
from alembic.config import Config
from pydantic import SecretStr
from sqlalchemy import Connection, Engine
from sqlalchemy.dialects.postgresql import insert
from testcontainers.community.postgres import PostgresContainer

from goblinshift.capacity import GLOBAL_STORAGE_MAX_BYTES, Resource
from goblinshift.capacity.tables import capacity_pool
from goblinshift.platform.db import make_engine
from goblinshift.platform.settings import Settings
from tests.skip_guard import pytest_addoption, pytest_configure  # noqa: F401 (hooks do guard)

POSTGRES_IMAGE = "postgres:18.6-trixie"


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


@pytest.fixture
def engine(db_settings: Settings) -> Iterator[Engine]:
    """Engine num banco já migrado até o head; quem desce o schema volta ao head no fim."""
    run_migrations(db_settings, "head")
    engine = make_engine(db_settings)
    yield engine
    engine.dispose()


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


# Os downgrades que apagariam linha abortam sem esta licença (#442, 47c). O container é da sessão
# e outros testes deixam linhas commitadas, então os helpers a dão por padrão; só os testes da
# guarda passam `allow_lossy=False`.
LOSSY_DOWNGRADE_ENV = "GS_ALLOW_LOSSY_DOWNGRADE"


def run_migrations(settings: Settings, target: str, *, allow_lossy: bool = True) -> None:
    """Roda o Alembic com as mesmas variáveis GS_* que a aplicação usa."""
    with _alembic_env(settings, allow_lossy=allow_lossy):
        if target == "base":
            command.downgrade(Config("alembic.ini"), "base")
        else:
            command.upgrade(Config("alembic.ini"), target)


def downgrade_migrations(settings: Settings, target: str, *, allow_lossy: bool = True) -> None:
    """Desce o schema até `target`, como `run_migrations` faz com "base"."""
    with _alembic_env(settings, allow_lossy=allow_lossy):
        command.downgrade(Config("alembic.ini"), target)


@contextmanager
def _alembic_env(settings: Settings, *, allow_lossy: bool = True) -> Iterator[None]:
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
        if allow_lossy:
            mp.setenv(LOSSY_DOWNGRADE_ENV, "1")
        else:
            # Sem herdar a licença do shell de quem roda os testes.
            mp.delenv(LOSSY_DOWNGRADE_ENV, raising=False)
        yield
