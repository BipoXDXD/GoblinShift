"""Retry das migrations que não conseguem o lock, sem banco (ADR 057)."""

import psycopg
import pytest
from alembic.util import CommandError
from sqlalchemy.exc import OperationalError

from goblinshift.platform.migration_locks import (
    LOCK_ATTEMPTS,
    MIGRATION_LOCK_TIMEOUT_MS,
    MIGRATION_STATEMENT_TIMEOUT_MS,
    is_lock_timeout,
    run_with_lock_retries,
    session_settings,
)

FIRST_DELAY_S = 1.0


def _lock_timeout() -> OperationalError:
    return OperationalError("ALTER TABLE run ...", None, psycopg.errors.LockNotAvailable())


def _statement_timeout() -> OperationalError:
    return OperationalError("CREATE INDEX ...", None, psycopg.errors.QueryCanceled())


class _Migrate:
    """Falha com os erros dados, na ordem, e depois conclui."""

    def __init__(self, *errors: Exception) -> None:
        self.errors = list(errors)
        self.calls = 0

    def __call__(self) -> None:
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)


def _run(migrate: _Migrate, sleeps: list[float], attempts: int = 3) -> None:
    run_with_lock_retries(
        migrate,
        attempts=attempts,
        first_delay_s=FIRST_DELAY_S,
        jitter=lambda delay_s: delay_s * 0.75,
        sleep=sleeps.append,
    )


def test_tempos_das_migrations_sao_finitos_e_o_lock_e_curto() -> None:
    assert MIGRATION_LOCK_TIMEOUT_MS == 5_000
    assert MIGRATION_STATEMENT_TIMEOUT_MS == 15 * 60_000
    assert LOCK_ATTEMPTS == 3


def test_sessao_da_migration_fixa_lock_e_statement_timeout() -> None:
    assert session_settings(300) == [
        "SET lock_timeout = 300",
        f"SET statement_timeout = {MIGRATION_STATEMENT_TIMEOUT_MS}",
    ]


@pytest.mark.parametrize("lock_timeout_ms", [0, -1])
def test_lock_timeout_precisa_ser_positivo(lock_timeout_ms: int) -> None:
    # 0 no PostgreSQL desliga o limite: a migration voltaria a esperar o lock para sempre.
    with pytest.raises(ValueError, match="lock_timeout"):
        session_settings(lock_timeout_ms)


def test_so_o_erro_de_lock_conta_como_lock_timeout() -> None:
    assert is_lock_timeout(_lock_timeout())
    assert not is_lock_timeout(_statement_timeout())
    assert not is_lock_timeout(RuntimeError("outro"))


def test_migration_que_consegue_o_lock_roda_uma_vez_sem_espera() -> None:
    migrate = _Migrate()
    sleeps: list[float] = []

    _run(migrate, sleeps)

    assert (migrate.calls, sleeps) == (1, [])


def test_lock_ocupado_repete_com_espera_dobrada_e_sorteada() -> None:
    migrate = _Migrate(_lock_timeout(), _lock_timeout())
    sleeps: list[float] = []

    _run(migrate, sleeps)

    assert migrate.calls == 3
    assert sleeps == [0.75, 1.5]


def test_lock_ocupado_em_todas_as_tentativas_falha_com_mensagem_clara() -> None:
    migrate = _Migrate(_lock_timeout(), _lock_timeout(), _lock_timeout())
    sleeps: list[float] = []

    with pytest.raises(CommandError, match="lock ocupado em 3 tentativas") as raised:
        _run(migrate, sleeps)

    assert migrate.calls == 3
    assert sleeps == [0.75, 1.5]
    assert isinstance(raised.value.__cause__, OperationalError)


def test_outro_erro_nao_repete() -> None:
    # Statement timeout é migration lenta, não lock disputado: repetir só gastaria mais tempo.
    migrate = _Migrate(_statement_timeout())
    sleeps: list[float] = []

    with pytest.raises(OperationalError):
        _run(migrate, sleeps)

    assert (migrate.calls, sleeps) == (1, [])


@pytest.mark.parametrize("attempts", [0, -1])
def test_numero_de_tentativas_precisa_ser_positivo(attempts: int) -> None:
    with pytest.raises(ValueError, match="attempts"):
        _run(_Migrate(), [], attempts=attempts)
