"""Parada do worker pedida por sinal, sem banco (complemento da triagem lote 2, L5).

O handler registrava a parada no log: escrever no stream dentro do handler pode reentrar numa
escrita que o próprio sinal interrompeu (`RuntimeError: reentrant call`, levantado no ponto
interrompido do laço). Agora o handler só marca a `ShutdownFlag`; o laço registra a parada uma
vez ao sair, e a espera por NOTIFY dorme em fatias para ver a flag mesmo com polling longo.
"""

import logging
import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from types import TracebackType
from typing import Self
from unittest.mock import Mock

import pytest

from goblinshift.entrypoints import worker
from goblinshift.entrypoints.shutdown import SHUTDOWN_POLL_S, ShutdownFlag
from goblinshift.platform.settings import Settings

STOP_MESSAGE = "parada pedida"
# Prazo do `docker stop` sem `stop_grace_period`: o worker ocioso sai bem antes dele.
STOP_DEADLINE_S = 10.0


# O handler é o de entrypoints/shutdown.py, com os testes de "só marca" e "não escreve no log" em
# tests/runs/test_process_shutdown.py; o teste do processo real, no fim, prova que o worker o usa.


# Laço ------------------------------------------------------------------------------------------


class FakeListener:
    """`QueueListener` sem banco: cada espera chama `on_wait` com o número da chamada e o prazo."""

    def __init__(self, on_wait: Callable[[int, float], bool]) -> None:
        self.waits: list[float] = []
        self._on_wait = on_wait

    def __call__(self, _engine: object, _connect_timeout_s: int) -> Self:
        return self

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        return None

    def wait(self, timeout_s: float) -> bool:
        self.waits.append(timeout_s)
        return self._on_wait(len(self.waits), timeout_s)


def _serve_idle(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    shutdown: ShutdownFlag,
    listener: FakeListener,
    poll_interval_s: float = 2.0,
) -> None:
    """Roda o `serve` real com a fila sempre vazia, sem banco (o engine nunca conecta)."""
    monkeypatch.setattr(worker, "QueueListener", listener)
    monkeypatch.setattr(worker, "_work_once", lambda *_args: False)
    settings = Settings(
        environment="test",
        db_port=1,
        storage_root=tmp_path / "storage",
        worker_poll_interval_s=poll_interval_s,
    )
    worker.serve(settings, Mock(), shutdown)


def test_serve_registra_a_parada_uma_vez_ao_sair(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    shutdown = ShutdownFlag()

    def signal_on_third_wait(count: int, _timeout_s: float) -> bool:
        if count == 3:
            shutdown.request(signal.SIGTERM)  # como o handler: só marca
        return False

    with caplog.at_level(logging.INFO, logger="goblinshift.worker"):
        _serve_idle(monkeypatch, tmp_path, shutdown, FakeListener(signal_on_third_wait))

    messages = [record.getMessage() for record in caplog.records]
    stops = [record for record in caplog.records if record.getMessage() == STOP_MESSAGE]
    assert len(stops) == 1
    assert stops[0].fields == {"signal": signal.SIGTERM}  # type: ignore[attr-defined]
    assert messages.index(STOP_MESSAGE) < messages.index("worker encerrado")


def test_serve_sem_sinal_nao_registra_parada(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # A saída por erro não é parada pedida: o laço só registra o que viu na flag.
    shutdown = ShutdownFlag()

    def fail(_count: int, _timeout_s: float) -> bool:
        raise RuntimeError("listener quebrado")

    with (
        caplog.at_level(logging.INFO, logger="goblinshift.worker"),
        pytest.raises(RuntimeError),
    ):
        _serve_idle(monkeypatch, tmp_path, shutdown, FakeListener(fail))

    assert STOP_MESSAGE not in [record.getMessage() for record in caplog.records]


def test_espera_por_notify_dorme_em_fatias_e_ve_a_parada(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Com polling de 60 s, uma espera inteira passaria do prazo de parada do deploy.
    shutdown = ShutdownFlag()

    def signal_on_second_wait(count: int, _timeout_s: float) -> bool:
        if count == 2:
            shutdown.request(signal.SIGTERM)
        return False

    listener = FakeListener(signal_on_second_wait)

    _serve_idle(monkeypatch, tmp_path, shutdown, listener, poll_interval_s=60)

    assert len(listener.waits) == 2
    assert max(listener.waits) <= SHUTDOWN_POLL_S


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def test_espera_sem_notify_cobre_o_intervalo_em_fatias() -> None:
    clock = FakeClock()
    waits: list[float] = []

    def wait(timeout_s: float) -> bool:
        waits.append(timeout_s)
        clock.now += timeout_s
        return False

    worker.wait_for_work(wait, ShutdownFlag(), 2.0, clock=clock)

    assert sum(waits) == pytest.approx(2.0)
    assert max(waits) <= SHUTDOWN_POLL_S


def test_notify_encerra_a_espera_na_hora() -> None:
    waits: list[float] = []

    def wait(timeout_s: float) -> bool:
        waits.append(timeout_s)
        return True

    worker.wait_for_work(wait, ShutdownFlag(), 60.0)

    assert len(waits) == 1


def test_parada_ja_pedida_nao_espera() -> None:
    shutdown = ShutdownFlag()
    shutdown.request(signal.SIGTERM)
    waits: list[float] = []

    def wait(timeout_s: float) -> bool:
        waits.append(timeout_s)
        return False

    worker.wait_for_work(wait, shutdown, 60.0)

    assert waits == []


# Processo real ---------------------------------------------------------------------------------

# Worker de verdade, com o handler de verdade, sem banco: o listener espera dormindo e a fila está
# sempre vazia. O polling de 60 s mostra que a parada não depende do fim da espera.
IDLE_WORKER = """
import sys, time
from unittest.mock import Mock
from goblinshift.entrypoints import worker
from goblinshift.platform.settings import Settings
from goblinshift.platform.telemetry import configure_logging

class SleepingListener:
    def __init__(self, *_args): pass
    def __enter__(self): return self
    def __exit__(self, *_exc): return None
    def wait(self, timeout_s):
        time.sleep(timeout_s)
        return False

configure_logging()
worker.QueueListener = SleepingListener
worker._work_once = lambda *_args: False
settings = Settings(
    environment="test", db_port=1, storage_root=sys.argv[1], worker_poll_interval_s=60
)
worker.serve(settings, Mock(), worker.shutdown_on_signals())
"""


def _wait_for_output(log: Path, text: str, timeout_s: float = 30.0) -> None:
    deadline = time.monotonic() + timeout_s
    while text not in log.read_text(encoding="utf-8"):
        assert time.monotonic() < deadline, f"{text!r} não apareceu a tempo"
        time.sleep(0.05)


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGINT])
def test_sinal_real_para_o_worker_ocioso_no_prazo(tmp_path: Path, signum: signal.Signals) -> None:
    log = tmp_path / "worker.log"
    env = {**os.environ, "GS_ENVIRONMENT": "test"}
    with log.open("wb") as output:
        process = subprocess.Popen(  # noqa: S603 - script do próprio teste
            [sys.executable, "-B", "-c", IDLE_WORKER, str(tmp_path / "storage")],
            env=env,
            stdout=output,
            stderr=output,
        )
    try:
        _wait_for_output(log, "worker pronto")
        started = time.monotonic()
        process.send_signal(signum)
        code = process.wait(timeout=STOP_DEADLINE_S)
        elapsed = time.monotonic() - started
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()

    output_text = log.read_text(encoding="utf-8")
    assert code == 0, output_text
    assert elapsed < 2 * SHUTDOWN_POLL_S + 1, output_text
    assert output_text.count(STOP_MESSAGE) == 1, output_text
    assert "worker encerrado" in output_text
    assert "Traceback" not in output_text
