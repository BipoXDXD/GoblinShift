"""Parada do worker pedida por sinal, sem banco (complemento da triagem lote 2, L5).

O handler registrava a parada no log: escrever no stream dentro do handler pode reentrar numa
escrita que o próprio sinal interrompeu (`RuntimeError: reentrant call`, levantado no ponto
interrompido do laço). Agora o handler só marca a `ShutdownFlag`; o laço registra a parada uma
vez ao sair, e a espera por NOTIFY dorme em fatias para ver a flag mesmo com polling longo.
"""

import json
import logging
import os
import signal
import subprocess
import sys
import time
import uuid
from collections.abc import Callable, Iterator
from datetime import timedelta
from pathlib import Path
from types import TracebackType
from typing import Self
from unittest.mock import Mock

import pytest
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.entrypoints import worker
from goblinshift.entrypoints.shutdown import SHUTDOWN_POLL_S, ShutdownFlag
from goblinshift.files.storage import FileStorage
from goblinshift.platform.db import unit_of_work
from goblinshift.platform.settings import Settings
from goblinshift.runs.lifecycle import MIN_RETRY_JITTER, ErrorCode
from goblinshift.runs.queries import get_run
from goblinshift.runs.store import container_name, load_run_any_owner
from goblinshift.runs.worker.coordinator import (
    SHUTDOWN_GRACE,
    Coordinator,
    CoordinatorConfig,
)
from goblinshift.runs.worker.runner import ExecutorPhase, ExecutorStatus
from tests.runs.worker_support import (
    LEASE,
    attempts,
    claim,
    create_user,
    queue_run,
    run_row,
)

STOP_MESSAGE = "parada pedida"
# Prazo do `docker stop` sem `stop_grace_period`: o worker ocioso sai bem antes dele.
STOP_DEADLINE_S = 10.0


# Teto de voltas dos falsos: uma regressão que não para o laço falha em vez de prender o teste.
MAX_CALLS = 100
# Linha fixa que o handler do worker escreve direto no fd 2 (sem o logging).
STOP_NOTICE = "gs-worker: sinal de parada recebido\n"


@pytest.fixture
def restored_handlers() -> Iterator[None]:
    previous = {signum: signal.getsignal(signum) for signum in (signal.SIGTERM, signal.SIGINT)}
    yield
    for signum, handler in previous.items():
        signal.signal(signum, handler)


# Handler ---------------------------------------------------------------------------------------


@pytest.mark.usefixtures("restored_handlers")
@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGINT])
def test_sinal_marca_a_parada_do_worker(signum: signal.Signals) -> None:
    shutdown = worker.shutdown_on_signals()

    signal.raise_signal(signum)

    assert shutdown.is_set()
    assert shutdown.signum == signum


@pytest.mark.usefixtures("restored_handlers")
def test_handler_do_worker_so_escreve_a_linha_fixa_no_fd_2(
    caplog: pytest.LogCaptureFixture, capfd: pytest.CaptureFixture[str]
) -> None:
    # O log estruturado da parada só sai quando o laço termina, depois da tentativa em curso; numa
    # tentativa longa o SIGKILL chega antes. A linha fixa, por `os.write`, não passa pelo logging
    # nem pelo buffer do stream, que o sinal pode ter interrompido.
    worker.shutdown_on_signals()
    capfd.readouterr()

    with caplog.at_level(logging.DEBUG):
        signal.raise_signal(signal.SIGTERM)

    captured = capfd.readouterr()
    assert caplog.records == []
    assert captured.err == STOP_NOTICE
    assert captured.out == ""


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
        assert len(self.waits) <= MAX_CALLS, "o laço não parou"
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
    turns: list[int] = []

    def empty_queue(*_args: object) -> bool:
        turns.append(len(turns) + 1)
        assert len(turns) <= MAX_CALLS, "o laço não parou"
        return False

    monkeypatch.setattr(worker, "_work_once", empty_queue)
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


def test_parada_durante_a_tentativa_termina_a_tentativa_e_nao_reivindica_outra(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # A regra principal: o sinal chega no meio da tentativa, que vai até o fim (a volta devolve
    # `True`, havia trabalho); o laço não começa outra volta, então não há segundo claim.
    shutdown = ShutdownFlag()
    turns: list[int] = []

    def attempt_interrupted_by_signal(*_args: object) -> bool:
        turns.append(len(turns) + 1)
        assert len(turns) <= MAX_CALLS, "o laço não parou"
        shutdown.request(signal.SIGTERM)  # chega durante a tentativa
        return True

    listener = FakeListener(lambda _count, _timeout_s: False)
    monkeypatch.setattr(worker, "QueueListener", listener)
    monkeypatch.setattr(worker, "_work_once", attempt_interrupted_by_signal)
    settings = Settings(environment="test", db_port=1, storage_root=tmp_path / "storage")

    with caplog.at_level(logging.INFO, logger="goblinshift.worker"):
        worker.serve(settings, Mock(), shutdown)

    messages = [record.getMessage() for record in caplog.records]
    assert turns == [1]
    assert listener.waits == []
    assert messages.count(STOP_MESSAGE) == 1


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
        assert len(waits) <= MAX_CALLS, "a espera não terminou"
        clock.now += timeout_s
        return False

    worker.wait_for_work(wait, ShutdownFlag(), 2.0, clock=clock)

    assert sum(waits) == pytest.approx(2.0)
    assert max(waits) <= SHUTDOWN_POLL_S


def test_notify_encerra_a_espera_na_hora() -> None:
    waits: list[float] = []

    def wait(timeout_s: float) -> bool:
        waits.append(timeout_s)
        assert len(waits) <= MAX_CALLS, "a espera não terminou"
        return True

    worker.wait_for_work(wait, ShutdownFlag(), 60.0)

    assert len(waits) == 1


def test_parada_ja_pedida_nao_espera() -> None:
    shutdown = ShutdownFlag()
    shutdown.request(signal.SIGTERM)
    waits: list[float] = []

    def wait(timeout_s: float) -> bool:
        waits.append(timeout_s)
        assert len(waits) <= MAX_CALLS, "a espera não terminou"
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
        # O prazo justo vale até o laço ver o sinal; a saída do interpretador, que sob carga leva
        # segundos, só conta no teto folgado do `wait`.
        _wait_for_output(log, STOP_MESSAGE, timeout_s=STOP_DEADLINE_S)
        elapsed = time.monotonic() - started
        code = process.wait(timeout=STOP_DEADLINE_S)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()

    output_text = log.read_text(encoding="utf-8")
    assert code == 0, output_text
    assert elapsed < 2 * SHUTDOWN_POLL_S + 1, output_text
    assert output_text.count(STOP_MESSAGE) == 1, output_text
    assert output_text.count(STOP_NOTICE) == 1, output_text
    assert "worker encerrado" in output_text
    assert "Traceback" not in output_text


# Cessão da tentativa longa no SIGTERM ----------------------------------------------------------


class _YieldingTestRunner:
    """Runner falso para testar a cessão: fica RUNNING até o `stop`, ou sai no 3º `status`."""

    def __init__(self, work_root: Path, shutdown: ShutdownFlag, *, stop_on_signal: bool) -> None:
        self._work_root = work_root
        self._shutdown = shutdown
        self._stop_on_signal = stop_on_signal
        self.status_calls = 0
        self.stop_calls: list[timedelta] = []
        self._stopped = False
        self._deadline = time.monotonic() + STOP_DEADLINE_S

    def work_dir(self, attempt_id: uuid.UUID) -> Path:
        return self._work_root / container_name(attempt_id)

    def create_work_dir(self, attempt_id: uuid.UUID) -> Path:
        root = self.work_dir(attempt_id)
        (root / "inputs").mkdir(parents=True, exist_ok=True)
        (root / "out").mkdir(parents=True, exist_ok=True)
        return root

    def list_work(self) -> list[uuid.UUID]:
        return []

    def start(self, attempt_id: uuid.UUID, image: str) -> ExecutorStatus:
        del attempt_id, image
        return ExecutorStatus(ExecutorPhase.RUNNING)

    def status(self, attempt_id: uuid.UUID) -> ExecutorStatus:
        assert time.monotonic() < self._deadline, "supervisão não terminou a tempo"
        self.status_calls += 1
        if self.status_calls == 1:
            self._shutdown.request(signal.SIGTERM)
        if self._stopped:
            return ExecutorStatus(ExecutorPhase.EXITED, exit_code=143)
        if not self._stop_on_signal and self.status_calls >= 3:
            # Simula o executor terminando logo antes da carência: falha determinística para
            # concluir sem depender de empacotamento completo de CSV.
            (self.work_dir(attempt_id) / "failure.json").write_text(
                json.dumps({"code": ErrorCode.OUTPUT_LIMIT_EXCEEDED}), encoding="utf-8"
            )
            return ExecutorStatus(ExecutorPhase.EXITED, exit_code=3)
        return ExecutorStatus(ExecutorPhase.RUNNING)

    def stop(self, attempt_id: uuid.UUID, grace: timedelta) -> ExecutorStatus:
        del attempt_id
        self.stop_calls.append(grace)
        self._stopped = True
        return ExecutorStatus(ExecutorPhase.EXITED, exit_code=143)

    def remove(self, attempt_id: uuid.UUID) -> None:
        del attempt_id


@pytest.mark.integration
@pytest.mark.parametrize(
    ("attempt_timeout", "yields"),
    [(SHUTDOWN_GRACE, False), (timedelta(minutes=10), True)],
    ids=["curta-nao-cede", "longa-cede"],
)
def test_parada_durante_tentativa_longa_cede_sem_gastar_tentativa(
    factory: sessionmaker[Session],
    storage: FileStorage,
    work_root: Path,
    attempt_timeout: timedelta,
    yields: bool,
) -> None:
    owner = create_user(factory)
    run_id = queue_run(factory, storage, owner)
    claimed = claim(factory, storage)
    shutdown = ShutdownFlag()
    runner = _YieldingTestRunner(work_root, shutdown, stop_on_signal=yields)
    config = CoordinatorConfig(
        lease=LEASE,
        status_poll=timedelta(seconds=0.02),
        stop_grace=timedelta(seconds=2),
        attempt_timeout=attempt_timeout,
        jitter=lambda: MIN_RETRY_JITTER,
    )
    worker_coord = Coordinator(
        factory,
        storage,
        runner,
        config,
        yield_requested=shutdown.is_set,
    )

    applied = worker_coord.run(claimed)

    assert applied is not None
    row = run_row(factory, run_id)
    [only_attempt] = attempts(factory, run_id)
    with unit_of_work(factory) as session:
        loaded = load_run_any_owner(session, run_id)
        view = get_run(session, owner, run_id)
    assert loaded is not None
    assert view is not None
    if yields:
        assert len(runner.stop_calls) == 1
        assert (row["status"], row["phase"]) == ("running", "retry_wait")
        assert loaded.state.physical_attempts == 0
        assert view.attempt == 0
        assert only_attempt["executor_started_at"] is not None
        assert only_attempt["yielded_at"] is not None
        assert only_attempt["ended_at"] is not None
        assert only_attempt["error_code"] is None
    else:
        assert runner.stop_calls == []
        # 3 consultas na supervisão até o executor sair, mais 1 em `remove_work`.
        assert runner.status_calls == 4
        assert (row["status"], row["error_code"]) == ("failed", ErrorCode.OUTPUT_LIMIT_EXCEEDED)
        assert loaded.state.physical_attempts == 1
        assert view.attempt == 1
        assert only_attempt["executor_started_at"] is not None
        assert only_attempt["yielded_at"] is None
        assert only_attempt["ended_at"] is not None
