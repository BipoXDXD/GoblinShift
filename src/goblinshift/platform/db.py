"""Engine, sessões, unidade de trabalho e a leitura dos erros do banco (docs/GoblinShiftPlan.md,
3.2, 3.7)."""

import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager

import psycopg
from sqlalchemy import Engine, MetaData, create_engine
from sqlalchemy.exc import DBAPIError, OperationalError
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

# SQLSTATE de conexão (classe 08) e de servidor parando (admin_shutdown, crash_shutdown,
# cannot_connect_now): o banco não vai responder ao passo seguinte desta volta.
_CONNECTION_EXCEPTION_CLASS = "08"
_SERVER_STOPPING_SQLSTATES = frozenset({"57P01", "57P02", "57P03"})
# O psycopg põe em `OperationalError` também erros de programação (55000, 3B001, 2F005, 54000...);
# só estes SQLSTATEs dizem que repetir a requisição pode dar certo. 40001 e 40P01 desfazem a
# transação inteira, e é a contenção que os causa: chegam aqui ao esgotar as tentativas de
# `runs/store.py` ou em rota sem retry.
_UNAVAILABLE_SQLSTATE_CLASSES = frozenset({"08", "53", "57"})
_UNAVAILABLE_SQLSTATES = frozenset({"40001", "40P01", "55P03"})
# Da classe 08, mas é parâmetro errado no SQL (`bind message supplies...`), não falha de conexão.
_PROTOCOL_VIOLATION_SQLSTATE = "08P01"
_SQLSTATE_CLASS_LENGTH = 2


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


def is_connection_lost(error: Exception) -> bool:
    """Erro de conexão: repetir o banco noutro passo só esperaria o `connect_timeout` de novo.

    Não é `is_unavailable_for_request`: aqui 08P01 (classe 08) conta como conexão perdida, e da
    classe 57 só o servidor parando (57P01-57P03) conta; o 57014 não.

    Conexão que o pool invalidou, pool esgotado (`TimeoutError` do SQLAlchemy, que não é
    `DBAPIError`: o pool desiste antes de pedir conexão, e o passo seguinte esperaria o
    `pool_timeout` de novo), ou `OperationalError` sem SQLSTATE (o `connect` recusado ou sem
    resposta) ou com SQLSTATE de conexão ou de servidor parando. `statement_timeout` (57014)
    também chega como `OperationalError`, mas o banco respondeu: os outros passos seguem.
    """
    if isinstance(error, PoolTimeoutError):
        return True
    if isinstance(error, DBAPIError) and error.connection_invalidated:
        return True
    if not isinstance(error, OperationalError):
        return False
    sqlstate: str | None = getattr(error.orig, "sqlstate", None)
    return (
        sqlstate is None
        or sqlstate.startswith(_CONNECTION_EXCEPTION_CLASS)
        or sqlstate in _SERVER_STOPPING_SQLSTATES
    )


def is_unavailable_for_request(error: Exception) -> bool:
    """O banco não atende agora, e a mesma requisição, repetida depois, pode dar certo.

    Não é `is_connection_lost`: aqui 08P01 é parâmetro errado no SQL, e não indisponibilidade, e
    a classe 57 inteira conta (o 57014 também), como 53300, 55P03, 40001 e 40P01.
    """
    if isinstance(error, PoolTimeoutError):
        return True
    # A conexão perdida vale para qualquer classe do driver: 25P03 chega como `InternalError`.
    if isinstance(error, DBAPIError) and error.connection_invalidated:
        return True
    if not isinstance(error, OperationalError):
        return False
    sqlstate: str | None = getattr(getattr(error, "orig", None), "sqlstate", None)
    # Sem SQLSTATE o servidor não chegou a responder: falha ao conectar ou conexão perdida.
    if sqlstate is None:
        return True
    if sqlstate == _PROTOCOL_VIOLATION_SQLSTATE:
        return False
    return (
        sqlstate in _UNAVAILABLE_SQLSTATES
        or sqlstate[:_SQLSTATE_CLASS_LENGTH] in _UNAVAILABLE_SQLSTATE_CLASSES
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
