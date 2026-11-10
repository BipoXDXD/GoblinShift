"""Listener do NOTIFY que sobrevive à queda do banco (triagem lote 2, L8).

Sem banco: `psycopg.connect` devolve conexões falsas, e relógio, espera e jitter são injetados.
Antes, um PostgreSQL reiniciado com o worker ocioso fazia `notifies` levantar `OperationalError`
fora do `try` da volta, e o processo saía com traceback.
"""

import logging
from collections.abc import Iterator
from datetime import timedelta

import psycopg
import pytest
from sqlalchemy import create_engine

from goblinshift.runs.worker.claim import RUNS_CHANNEL, QueueListener, ReconnectBackoff

CLAIM_LOGGER = "goblinshift.runs.worker.claim"
LOST_MESSAGE = "listener perdeu a conexão; seguindo no polling"
RETRY_MESSAGE = "listener sem conexão; nova tentativa adiada"
RECONNECTED_MESSAGE = "listener reconectado"
DB_CANARY = "CANARY-host-db-interno"
POLL_S = 2.0
NO_JITTER = ReconnectBackoff(
    first=timedelta(seconds=1), cap=timedelta(seconds=30), jitter=lambda delay: delay
)


def connection_lost() -> psycopg.OperationalError:
    return psycopg.OperationalError(f'server at "{DB_CANARY}" closed the connection unexpectedly')


class FakeConnection:
    def __init__(self, notifications: int = 0, error: BaseException | None = None) -> None:
        self.notifications = notifications
        self.error = error
        self.executed: list[str] = []
        self.closed = False

    def execute(self, query: str) -> None:
        self.executed.append(query)

    def notifies(self, *, timeout: float, stop_after: int) -> Iterator[object]:
        if self.error is not None:
            raise self.error
        yield from range(min(self.notifications, stop_after))

    def close(self) -> None:
        self.closed = True


class FakeConnector:
    """Fila de resultados de `psycopg.connect`: uma conexão ou um erro a levantar."""

    def __init__(self, *outcomes: FakeConnection | BaseException) -> None:
        self.outcomes = list(outcomes)
        self.calls = 0

    def __call__(self, *_args: object, **_kwargs: object) -> FakeConnection:
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def slept() -> list[float]:
    return []


def listener(
    clock: FakeClock, slept: list[float], backoff: ReconnectBackoff = NO_JITTER
) -> QueueListener:
    engine = create_engine("postgresql+psycopg://gs:segredo@db-interno:5432/gs")
    return QueueListener(engine, 5, backoff=backoff, clock=clock, sleep=slept.append)


def connect_with(monkeypatch: pytest.MonkeyPatch, connector: FakeConnector) -> FakeConnector:
    monkeypatch.setattr(psycopg, "connect", connector)
    return connector


def messages(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [record.getMessage() for record in caplog.records if record.name == CLAIM_LOGGER]


# Conexão viva -----------------------------------------------------------------------------


def test_notify_acorda_a_espera(
    monkeypatch: pytest.MonkeyPatch, clock: FakeClock, slept: list[float]
) -> None:
    connect_with(monkeypatch, FakeConnector(FakeConnection(notifications=1)))

    with listener(clock, slept) as queue:
        assert queue.wait(POLL_S) is True


def test_sem_notify_a_espera_esgota(
    monkeypatch: pytest.MonkeyPatch, clock: FakeClock, slept: list[float]
) -> None:
    connection = FakeConnection()
    connect_with(monkeypatch, FakeConnector(connection))

    with listener(clock, slept) as queue:
        assert queue.wait(POLL_S) is False

    assert connection.executed == [f"LISTEN {RUNS_CHANNEL}"]
    assert connection.closed


def test_espera_fora_do_bloco_with_e_erro(clock: FakeClock, slept: list[float]) -> None:
    with pytest.raises(RuntimeError):
        listener(clock, slept).wait(POLL_S)


# Queda da conexão -------------------------------------------------------------------------


def test_conexao_perdida_na_espera_nao_derruba_o_worker(
    monkeypatch: pytest.MonkeyPatch,
    clock: FakeClock,
    slept: list[float],
    caplog: pytest.LogCaptureFixture,
) -> None:
    lost = FakeConnection(error=connection_lost())
    connect_with(monkeypatch, FakeConnector(lost))

    with caplog.at_level(logging.INFO, logger=CLAIM_LOGGER), listener(clock, slept) as queue:
        assert queue.wait(POLL_S) is False

    assert lost.closed
    [record] = [r for r in caplog.records if r.getMessage() == LOST_MESSAGE]
    assert record.fields == {"error_type": "OperationalError", "sqlstate": None}  # type: ignore[attr-defined]
    assert DB_CANARY not in caplog.text


def test_depois_da_queda_a_espera_seguinte_reconecta_e_volta_a_ouvir(
    monkeypatch: pytest.MonkeyPatch,
    clock: FakeClock,
    slept: list[float],
    caplog: pytest.LogCaptureFixture,
) -> None:
    fresh = FakeConnection(notifications=1)
    connector = connect_with(
        monkeypatch, FakeConnector(FakeConnection(error=connection_lost()), fresh)
    )

    with caplog.at_level(logging.INFO, logger=CLAIM_LOGGER), listener(clock, slept) as queue:
        queue.wait(POLL_S)
        assert queue.wait(POLL_S) is True

    assert connector.calls == 2
    assert fresh.executed == [f"LISTEN {RUNS_CHANNEL}"]
    assert RECONNECTED_MESSAGE in messages(caplog)
    assert slept == []


def test_banco_ainda_fora_a_espera_vira_polling_e_adia_a_tentativa(
    monkeypatch: pytest.MonkeyPatch,
    clock: FakeClock,
    slept: list[float],
    caplog: pytest.LogCaptureFixture,
) -> None:
    connector = connect_with(
        monkeypatch,
        FakeConnector(FakeConnection(error=connection_lost()), connection_lost(), FakeConnection()),
    )

    with caplog.at_level(logging.INFO, logger=CLAIM_LOGGER), listener(clock, slept) as queue:
        queue.wait(POLL_S)
        assert queue.wait(POLL_S) is False  # reconexão recusada: 1 s até a próxima
        clock.now += 0.5
        assert queue.wait(POLL_S) is False  # ainda dentro da espera: nem tenta
        attempts_before_retry = connector.calls
        clock.now += 0.5
        queue.wait(POLL_S)

    assert attempts_before_retry == 2
    assert connector.calls == 3
    assert slept == [POLL_S, POLL_S]
    [record] = [r for r in caplog.records if r.getMessage() == RETRY_MESSAGE]
    assert record.fields == {  # type: ignore[attr-defined]
        "error_type": "OperationalError",
        "sqlstate": None,
        "failures": 1,
        "retry_in_s": 1.0,
    }
    assert DB_CANARY not in caplog.text


def test_listen_que_falha_na_reconexao_fecha_a_conexao_nova(
    monkeypatch: pytest.MonkeyPatch, clock: FakeClock, slept: list[float]
) -> None:
    class RefusingListen(FakeConnection):
        def execute(self, query: str) -> None:
            raise connection_lost()

    half_open = RefusingListen()
    connect_with(monkeypatch, FakeConnector(FakeConnection(error=connection_lost()), half_open))

    with listener(clock, slept) as queue:
        queue.wait(POLL_S)
        assert queue.wait(POLL_S) is False

    assert half_open.closed


def test_reconexao_bem_sucedida_zera_o_backoff(
    monkeypatch: pytest.MonkeyPatch,
    clock: FakeClock,
    slept: list[float],
    caplog: pytest.LogCaptureFixture,
) -> None:
    connect_with(
        monkeypatch,
        FakeConnector(
            FakeConnection(error=connection_lost()),
            connection_lost(),
            FakeConnection(error=connection_lost()),
            connection_lost(),
        ),
    )

    with caplog.at_level(logging.INFO, logger=CLAIM_LOGGER), listener(clock, slept) as queue:
        queue.wait(POLL_S)  # perde
        queue.wait(POLL_S)  # recusada: 1 s
        clock.now += 1
        queue.wait(POLL_S)  # reconecta e perde de novo
        queue.wait(POLL_S)  # recusada: de novo 1 s, não 2 s

    retries = [r.fields["retry_in_s"] for r in caplog.records if r.getMessage() == RETRY_MESSAGE]  # type: ignore[attr-defined]
    assert retries == [1.0, 1.0]


@pytest.mark.parametrize("interrupt", [KeyboardInterrupt(), SystemExit(1)])
def test_interrupcao_na_espera_nao_e_engolida(
    monkeypatch: pytest.MonkeyPatch, clock: FakeClock, slept: list[float], interrupt: BaseException
) -> None:
    connect_with(monkeypatch, FakeConnector(FakeConnection(error=interrupt)))

    with listener(clock, slept) as queue, pytest.raises(type(interrupt)):
        queue.wait(POLL_S)


# Backoff ----------------------------------------------------------------------------------


def test_backoff_dobra_ate_o_teto() -> None:
    assert [NO_JITTER.delay_s(failures) for failures in range(1, 8)] == [
        1.0,
        2.0,
        4.0,
        8.0,
        16.0,
        30.0,
        30.0,
    ]


def test_backoff_nao_estoura_depois_de_muitas_falhas() -> None:
    assert NO_JITTER.delay_s(100_000) == 30.0


def test_backoff_aplica_o_jitter_injetado() -> None:
    halved = ReconnectBackoff(
        first=timedelta(seconds=1), cap=timedelta(seconds=30), jitter=lambda delay: delay / 2
    )

    assert halved.delay_s(3) == 2.0


@pytest.mark.parametrize("failures", [1, 3, 20])
def test_jitter_padrao_fica_entre_metade_e_a_espera_inteira(failures: int) -> None:
    backoff = ReconnectBackoff()
    full = ReconnectBackoff(jitter=lambda delay: delay).delay_s(failures)

    for _ in range(200):
        assert full / 2 <= backoff.delay_s(failures) <= full


def test_backoff_com_falhas_menores_que_um_e_erro() -> None:
    with pytest.raises(ValueError, match="falhas"):
        NO_JITTER.delay_s(0)
