"""Pausa entre retentativas de `run_transaction` com jitter (auditoria L3).

Sem banco: a sessão é falsa e o sorteio e o sono são injetados. Retentativas de workers
concorrentes que dormem o mesmo tempo voltam a colidir juntas; o jitter as espalha.
"""

from types import SimpleNamespace
from typing import Any, cast

import pytest
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.runs import store
from goblinshift.runs.store import MAX_TRANSACTION_ATTEMPTS, retry_pause_s, run_transaction

BASE_S = store.RETRY_BASE_PAUSE_S


class _FakeSession:
    def __enter__(self) -> _FakeSession:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def begin(self) -> _FakeSession:
        return self


def _deadlock() -> DBAPIError:
    return DBAPIError("SELECT 1", None, cast(Any, SimpleNamespace(sqlstate="40P01")))


def _factory() -> sessionmaker[Session]:
    return cast(Any, _FakeSession)


@pytest.mark.parametrize("attempt_number", [1, 2])
def test_pausa_difere_entre_sorteios_diferentes(attempt_number: int) -> None:
    first = retry_pause_s(attempt_number, draw=lambda: 0.1)
    second = retry_pause_s(attempt_number, draw=lambda: 0.9)

    assert first != second


@pytest.mark.parametrize("attempt_number", [1, 2, 3])
@pytest.mark.parametrize("draw_value", [0.0, 0.5, 0.999999])
def test_pausa_fica_entre_metade_do_backoff_e_o_backoff(
    attempt_number: int, draw_value: float
) -> None:
    ceiling = BASE_S * attempt_number

    pause = retry_pause_s(attempt_number, draw=lambda: draw_value)

    assert ceiling / 2 <= pause <= ceiling


def test_pausa_extremos_sao_literais() -> None:
    assert retry_pause_s(2, draw=lambda: 0.0) == pytest.approx(0.05)
    assert retry_pause_s(2, draw=lambda: 1.0) == pytest.approx(0.10)


def test_run_transaction_dorme_a_pausa_sorteada_entre_retentativas() -> None:
    calls = {"n": 0}
    slept: list[float] = []

    def work(_session: Session) -> str:
        calls["n"] += 1
        if calls["n"] < MAX_TRANSACTION_ATTEMPTS:
            raise _deadlock()
        return "ok"

    result = run_transaction(_factory(), work, draw=lambda: 0.0, sleep=slept.append)

    assert result == "ok"
    assert slept == [pytest.approx(BASE_S / 2), pytest.approx(BASE_S)]
