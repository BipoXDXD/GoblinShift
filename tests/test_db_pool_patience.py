"""Paciência com o pool nas escritas que encerram uma transferência (platform/db.py).

A API espera uma conexão só meio segundo (`API_DB_POOL_TIMEOUT_S`), mas a escrita que conclui um
envio ou devolve a vaga não pode desistir tão cedo: perderia uma transferência já feita e
prenderia a vaga até o lease vencer. O `TimeoutError` do pool sai no checkout, antes de qualquer
SQL, então repetir a operação inteira é seguro.
"""

import pytest
from sqlalchemy.exc import OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError

from goblinshift.platform.db import retry_on_pool_timeout

ATTEMPT_S = 0.5  # cada tentativa espera o `pool_timeout` da API antes de o pool recusar
PATIENCE_S = 5.0


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_repete_enquanto_o_pool_recusa_e_devolve_o_resultado() -> None:
    clock = FakeClock()
    refusals = 3

    def operation() -> str:
        nonlocal refusals
        if refusals:
            refusals -= 1
            clock.now += ATTEMPT_S
            raise PoolTimeoutError("pool esgotado")
        return "feito"

    assert retry_on_pool_timeout(operation, patience_s=PATIENCE_S, clock=clock) == "feito"
    assert refusals == 0


def test_desiste_quando_passa_da_paciencia() -> None:
    clock = FakeClock()

    def operation() -> None:
        clock.now += ATTEMPT_S
        raise PoolTimeoutError("pool esgotado")

    with pytest.raises(PoolTimeoutError):
        retry_on_pool_timeout(operation, patience_s=PATIENCE_S, clock=clock)

    assert clock.now == PATIENCE_S


def test_outro_erro_do_banco_nao_e_repetido() -> None:
    attempts = 0

    def operation() -> None:
        nonlocal attempts
        attempts += 1
        raise OperationalError("SELECT 1", {}, Exception("conexão caiu"))

    with pytest.raises(OperationalError):
        retry_on_pool_timeout(operation, patience_s=PATIENCE_S, clock=FakeClock())

    assert attempts == 1
