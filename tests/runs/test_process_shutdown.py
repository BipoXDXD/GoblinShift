"""Flag de parada dos processos de fundo (entrypoints/shutdown.py; triagem lote 2, L5).

O handler chamava `threading.Event.set()`, que toma a trava interna do Event. O CPython roda o
handler na thread principal, entre dois bytecodes: se o sinal chega enquanto a própria
`Event.wait` do laço segura essa trava (ao entrar e ao sair da espera), o `set()` espera por ela
para sempre. Agora o handler só marca uma flag, e a espera do laço dorme em fatias curtas.
"""

import logging
import signal
from collections.abc import Callable, Iterator

import pytest

from goblinshift.entrypoints.shutdown import SHUTDOWN_POLL_S, ShutdownFlag, shutdown_on_signals

INTERVAL_S = 30.0


class FakeTime:
    """Relógio e `sleep` juntos: dormir avança o relógio."""

    def __init__(self) -> None:
        self.now = 500.0
        self.naps: list[float] = []
        self.on_nap: Callable[[int], None] = lambda _count: None

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.naps.append(seconds)
        self.now += seconds
        self.on_nap(len(self.naps))


@pytest.fixture
def fake_time() -> FakeTime:
    return FakeTime()


@pytest.fixture
def flag(fake_time: FakeTime) -> ShutdownFlag:
    return ShutdownFlag(clock=fake_time.clock, sleep=fake_time.sleep)


def test_sem_pedido_a_espera_dorme_o_intervalo_em_fatias(
    flag: ShutdownFlag, fake_time: FakeTime
) -> None:
    assert flag.wait(INTERVAL_S) is False

    assert sum(fake_time.naps) == pytest.approx(INTERVAL_S)
    assert max(fake_time.naps) <= SHUTDOWN_POLL_S


def test_pedido_durante_a_espera_acorda_na_fatia_seguinte(
    flag: ShutdownFlag, fake_time: FakeTime
) -> None:
    # Como um sinal: chega no meio da espera, e o handler só marca a flag.
    fake_time.on_nap = lambda count: flag.request(signal.SIGTERM) if count == 2 else None

    assert flag.wait(INTERVAL_S) is True

    assert len(fake_time.naps) == 2


def test_pedido_antes_da_espera_volta_sem_dormir(flag: ShutdownFlag, fake_time: FakeTime) -> None:
    flag.request(signal.SIGINT)

    assert flag.wait(INTERVAL_S) is True
    assert fake_time.naps == []
    assert flag.is_set()
    assert flag.signum == signal.SIGINT


def test_sem_pedido_a_flag_comeca_desligada(flag: ShutdownFlag) -> None:
    assert not flag.is_set()
    assert flag.signum is None


def test_espera_de_zero_nao_dorme(flag: ShutdownFlag, fake_time: FakeTime) -> None:
    assert flag.wait(0) is False
    assert fake_time.naps == []


@pytest.fixture
def restored_handlers() -> Iterator[None]:
    previous = {signum: signal.getsignal(signum) for signum in (signal.SIGTERM, signal.SIGINT)}
    yield
    for signum, handler in previous.items():
        signal.signal(signum, handler)


@pytest.mark.usefixtures("restored_handlers")
@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGINT])
def test_sinal_marca_a_parada(signum: signal.Signals) -> None:
    shutdown = shutdown_on_signals()

    signal.raise_signal(signum)

    assert shutdown.is_set()
    assert shutdown.signum == signum


@pytest.mark.usefixtures("restored_handlers")
def test_handler_nao_escreve_no_log(
    caplog: pytest.LogCaptureFixture, capfd: pytest.CaptureFixture[str]
) -> None:
    # Escrever no stream dentro do handler pode reentrar numa escrita interrompida do próprio
    # processo (`RuntimeError: reentrant call`); o laço registra a parada quando a vê.
    shutdown_on_signals()
    capfd.readouterr()

    with caplog.at_level(logging.DEBUG):
        signal.raise_signal(signal.SIGTERM)

    assert caplog.records == []
    assert capfd.readouterr() == ("", "")


@pytest.mark.usefixtures("restored_handlers")
def test_aviso_que_nao_consegue_escrever_nao_derruba_o_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Uma exceção no handler subiria no ponto interrompido do laço; a parada vale mesmo assim.
    def closed_stderr(_fd: int, _data: bytes) -> int:
        raise OSError("stderr fechado")

    monkeypatch.setattr("goblinshift.entrypoints.shutdown.os.write", closed_stderr)
    shutdown = shutdown_on_signals(notice=b"aviso\n")

    signal.raise_signal(signal.SIGTERM)

    assert shutdown.is_set()
