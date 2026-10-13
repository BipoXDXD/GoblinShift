from collections.abc import Iterator

import pytest
from alembic import command
from alembic.config import Config
from pydantic import SecretStr
from testcontainers.community.postgres import PostgresContainer

from goblinshift.platform.settings import Settings

POSTGRES_IMAGE = "postgres:18.6-trixie"


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


def run_migrations(settings: Settings, target: str) -> None:
    """Roda o Alembic com as mesmas variáveis GS_* que a aplicação usa."""
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
        if target == "base":
            command.downgrade(Config("alembic.ini"), "base")
        else:
            command.upgrade(Config("alembic.ini"), target)
