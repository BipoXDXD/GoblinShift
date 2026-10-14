"""Engine, sessões e unidade de trabalho (docs/GoblinShiftPlan.md, 3.2)."""

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, MetaData, create_engine
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.platform.settings import Settings

# Um MetaData para todas as tabelas: cada módulo declara as suas (`<módulo>/tables.py`), e o
# Alembic compara o banco com a união delas. Nomes de constraint previsíveis permitem que uma
# migração posterior as altere sem depender do nome que o PostgreSQL inventaria.
metadata = MetaData(
    naming_convention={
        "pk": "%(table_name)s_pkey",
        "uq": "%(table_name)s_%(column_0_N_name)s_key",
        "ix": "%(table_name)s_%(column_0_N_name)s_idx",
        "fk": "%(table_name)s_%(column_0_N_name)s_fkey",
        "ck": "%(table_name)s_%(constraint_name)s_check",
    }
)


def make_engine(settings: Settings) -> Engine:
    options = " ".join(
        [
            f"-c statement_timeout={settings.db_statement_timeout_ms}",
            f"-c lock_timeout={settings.db_lock_timeout_ms}",
            f"-c idle_in_transaction_session_timeout={settings.db_idle_in_transaction_timeout_ms}",
        ]
    )
    return create_engine(
        settings.database_url,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_pool_max_overflow,
        # Pool esgotado vira 503 rápido em vez de requisição pendurada.
        pool_timeout=settings.db_pool_timeout_s,
        pool_pre_ping=True,
        connect_args={"options": options, "connect_timeout": settings.db_connect_timeout_s},
    )


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)


@contextmanager
def unit_of_work(factory: sessionmaker[Session]) -> Iterator[Session]:
    """Abre uma transação; commit no sucesso, rollback em qualquer exceção."""
    with factory() as session, session.begin():
        yield session
