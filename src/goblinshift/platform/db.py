"""Engine, sessões e unidade de trabalho (docs/GoblinShiftPlan.md, 3.2)."""

import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager

import psycopg
from sqlalchemy import Engine, MetaData, create_engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
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


def is_lock_timeout(error: BaseException) -> bool:
    """O comando desistiu do lock (55P03), e não de outra coisa."""
    return isinstance(error, OperationalError) and isinstance(
        error.orig, psycopg.errors.LockNotAvailable
    )


def is_lock_contention(error: BaseException) -> bool:
    """Disputa passageira por linha travada: o `lock_timeout` (55P03) ou o `statement_timeout`
    (57014) estourou. O banco respondeu e outra tentativa, mais tarde, tende a passar."""
    return is_lock_timeout(error) or (
        isinstance(error, OperationalError) and isinstance(error.orig, psycopg.errors.QueryCanceled)
    )


def make_engine(settings: Settings) -> Engine:
    options = " ".join(
        [
            f"-c statement_timeout={settings.db_statement_timeout_ms}",
            f"-c lock_timeout={settings.db_lock_timeout_ms}",
            f"-c idle_in_transaction_session_timeout={settings.db_idle_in_transaction_timeout_ms}",
            # Datas saem em UTC em toda rota, qualquer que seja o fuso padrão do banco.
            "-c timezone=UTC",
        ]
    )
    return create_engine(
        settings.database_url,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_pool_max_overflow,
        # Pool esgotado vira 503 rápido em vez de requisição pendurada.
        pool_timeout=settings.db_pool_timeout_s,
        pool_pre_ping=True,
        # Sem os valores das consultas na mensagem dos erros, que vai para o log (3.7).
        hide_parameters=True,
        connect_args={"options": options, "connect_timeout": settings.db_connect_timeout_s},
    )


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)


@contextmanager
def unit_of_work(factory: sessionmaker[Session]) -> Iterator[Session]:
    """Abre uma transação; commit no sucesso, rollback em qualquer exceção."""
    with factory() as session, session.begin():
        yield session


def retry_on_pool_timeout[T](
    operation: Callable[[], T],
    *,
    patience_s: float,
    clock: Callable[[], float] = time.monotonic,
) -> T:
    """Repete `operation` enquanto o pool não entregar conexão, por até `patience_s` segundos.

    Para as escritas que encerram uma transferência, numa API cujo pool desiste rápido: concluir o
    envio ou devolver a vaga não pode falhar por um pico curto. Só serve a operações que abrem a
    própria transação: o `TimeoutError` do pool sai no checkout, antes de qualquer SQL, e nada fica
    pela metade. Cada tentativa já espera o `pool_timeout` do engine, que faz as vezes de intervalo.
    """
    deadline = clock() + patience_s
    while True:
        try:
            return operation()
        except PoolTimeoutError:
            if clock() >= deadline:
                raise
