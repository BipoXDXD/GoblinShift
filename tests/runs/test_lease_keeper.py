"""Limites monotônicos da renovação durante a coleta e a gravação de saídas."""

from collections.abc import Callable
from datetime import timedelta

import pytest
from sqlalchemy.exc import OperationalError, ProgrammingError

from goblinshift.runs.lifecycle import Status
from goblinshift.runs.store import LostFencing
from goblinshift.runs.worker.coordinator import CoordinatorConfig, _LeaseKeeper, _StopRequested

CONFIG = CoordinatorConfig(lease=timedelta(seconds=30))
RETRY_S = CONFIG.renew_retry_every.total_seconds()


class Clock:
    def __init__(self) -> None:
        self.now = 1_000.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def transient_error() -> OperationalError:
    return OperationalError(None, None, RuntimeError("banco indisponível"))


def keeper(renew: Callable[[], Status], clock: Clock, *, remaining: float = 20) -> _LeaseKeeper:
    lease = _LeaseKeeper(renew, CONFIG, clock=clock.monotonic, sleep=clock.sleep)
    lease.set_lease_deadline(clock.now + remaining)
    return lease


def test_falhas_transitorias_renovam_antes_de_entregar_os_blocos() -> None:
    clock = Clock()
    calls = 0

    def renew() -> Status:
        nonlocal calls
        calls += 1
        if calls <= 2:
            raise transient_error()
        return Status.RUNNING

    assert list(keeper(renew, clock).beating([b"a", b"b"])) == [b"a", b"b"]
    assert calls == 3
    assert clock.sleeps == [RETRY_S, RETRY_S]


def test_falha_continua_sobe_antes_da_margem_sem_entregar_blocos() -> None:
    clock = Clock()
    error = transient_error()

    def renew() -> Status:
        raise error

    chunks = keeper(renew, clock, remaining=3).beating([b"saida"])
    with pytest.raises(OperationalError) as failure:
        next(chunks)

    assert failure.value is error
    assert clock.sleeps == [RETRY_S]


def test_renovacao_confirmada_estende_a_janela_de_retry() -> None:
    clock = Clock()
    calls = 0

    def renew() -> Status:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise transient_error()
        return Status.RUNNING

    lease = keeper(renew, clock, remaining=5)
    lease.beat()
    clock.now += CONFIG.renew_every.total_seconds()
    lease.beat()

    assert calls == 3
    assert clock.sleeps == [RETRY_S]


@pytest.mark.parametrize(
    "outcome",
    [Status.STOPPING, LostFencing("outra geração"), ProgrammingError(None, None, ValueError())],
    ids=["parada", "fencing", "erro-de-programacao"],
)
def test_retry_nao_entrega_bloco_depois_de_parada_ou_erro_definitivo(
    outcome: Status | Exception,
) -> None:
    clock = Clock()
    calls = 0

    def renew() -> Status:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise transient_error()
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    expected = type(outcome) if isinstance(outcome, Exception) else _StopRequested
    with pytest.raises(expected):
        next(keeper(renew, clock).beating([b"saida"]))

    assert calls == 2
    assert clock.sleeps == [RETRY_S]


def test_keeper_sem_prazo_observado_propaga_o_primeiro_erro() -> None:
    clock = Clock()

    def renew() -> Status:
        raise transient_error()

    lease = _LeaseKeeper(renew, CONFIG, clock=clock.monotonic, sleep=clock.sleep)
    with pytest.raises(OperationalError):
        lease.beat()
    assert clock.sleeps == []


def test_retry_nao_considera_commit_ambiguo_como_renovacao() -> None:
    clock = Clock()
    calls = 0
    confirmed_deadline = clock.now + 3
    database_deadline = confirmed_deadline

    def renew() -> Status:
        nonlocal calls, database_deadline
        calls += 1
        if calls == 1:
            database_deadline = clock.now + CONFIG.lease.total_seconds()
        # O banco gravou um prazo novo, mas a resposta do commit não chegou ao worker.
        raise transient_error()

    lease = keeper(renew, clock, remaining=3)
    with pytest.raises(OperationalError):
        lease.beat()
    assert database_deadline > confirmed_deadline
    assert calls == 2
    assert clock.now == 1_001.0


def test_espera_que_ultrapassa_o_limite_nao_faz_nova_tentativa() -> None:
    clock = Clock()
    calls = 0

    def renew() -> Status:
        nonlocal calls
        calls += 1
        raise transient_error()

    def delayed_sleep(seconds: float) -> None:
        clock.sleep(seconds + 2)

    lease = _LeaseKeeper(renew, CONFIG, clock=clock.monotonic, sleep=delayed_sleep)
    lease.set_lease_deadline(clock.now + 3)
    with pytest.raises(OperationalError):
        lease.beat()
    assert calls == 1
