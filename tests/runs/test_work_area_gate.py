"""Claim só com a área de trabalho livre, inclusive depois de reinício do worker (#224, Ruling).

A pausa do #224 vive na memória do processo: um deploy ou SIGKILL dentro da janela a perde, e o
processo novo reivindicaria com a tentativa órfã ainda na área, gastando um claim com
`work_area_dirty`. O portão confere o estado da área (o mesmo que o `executor-ctl` confere no
`start`) antes de cada claim.

Sem banco: o Runner e o claim são falsos. O cenário com Postgres e o `executor-ctl` falso está em
`test_claim_pause_after_db_failure.py`.
"""

import logging
import time
import uuid
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import Mock

import pytest
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.entrypoints import worker as worker_entrypoint
from goblinshift.entrypoints.shutdown import ShutdownFlag
from goblinshift.files.storage import FileStorage
from goblinshift.platform.settings import Settings
from goblinshift.runs.finished import RunFinishedReporter
from goblinshift.runs.worker.claim import ClaimedAttempt
from goblinshift.runs.worker.claim_pause import ClaimPause
from goblinshift.runs.worker.coordinator import Coordinator
from goblinshift.runs.worker.runner import Runner
from goblinshift.runs.worker.work_area_gate import HOLD_LIMIT, WorkAreaGate

WORKER_LOGGER = "goblinshift.worker"
GATE_LOGGER = "goblinshift.runs.worker.work_area_gate"
OCCUPIED_MESSAGE = "claims esperando a área de trabalho"
FREED_MESSAGE = "área de trabalho livre; claims retomados"
UNREADABLE_MESSAGE = "área de trabalho ilegível; claims esperando"
LIMIT_MESSAGE = "área de trabalho presa além do teto; claims liberados"
TURN_FAILED_MESSAGE = "volta do worker falhou"
WINDOW = timedelta(seconds=100)
ORPHAN = uuid.UUID("00000000-0000-4000-8000-000000000001")


class FakeArea:
    """`Runner.list_work` sobre uma lista que o teste controla; `error` simula área ilegível."""

    def __init__(self, *attempt_ids: uuid.UUID, error: OSError | None = None) -> None:
        self.attempt_ids = list(attempt_ids)
        self.error = error
        self.reads = 0

    def list_work(self) -> list[uuid.UUID]:
        self.reads += 1
        if self.error is not None:
            raise self.error
        return sorted(self.attempt_ids)


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def gate_over(area: FakeArea, clock: FakeClock | None = None) -> WorkAreaGate:
    return WorkAreaGate(cast(Runner, area), clock=clock if clock is not None else FakeClock())


def unreadable() -> FakeArea:
    return FakeArea(error=PermissionError("sem acesso"))


def gate_records(caplog: pytest.LogCaptureFixture, message: str) -> list[logging.LogRecord]:
    return [record for record in caplog.records if record.getMessage() == message]


# Portão ----------------------------------------------------------------------------------


def test_area_vazia_libera_o_claim() -> None:
    assert gate_over(FakeArea()).allows_claim() is True


def test_area_com_tentativa_bloqueia_o_claim() -> None:
    assert gate_over(FakeArea(ORPHAN)).allows_claim() is False


def test_teto_e_de_duzentos_segundos() -> None:
    assert timedelta(seconds=200) == HOLD_LIMIT


def test_area_ilegivel_bloqueia_o_claim_sem_subir_o_erro() -> None:
    assert gate_over(unreadable()).allows_claim() is False


def test_area_ilegivel_registra_traceback_so_na_primeira_leitura(
    caplog: pytest.LogCaptureFixture,
) -> None:
    gate = gate_over(unreadable())

    with caplog.at_level(logging.DEBUG, logger=GATE_LOGGER):
        gate.allows_claim()
        gate.allows_claim()
        gate.allows_claim()

    [event] = caplog.records
    assert event.getMessage() == UNREADABLE_MESSAGE
    assert event.levelno == logging.WARNING
    assert event.exc_info is not None
    assert isinstance(event.exc_info[1], PermissionError)


def test_area_que_volta_a_ser_lida_e_esta_ocupada_registra_a_mudanca(
    caplog: pytest.LogCaptureFixture,
) -> None:
    area = unreadable()
    gate = gate_over(area)
    gate.allows_claim()
    area.error = None
    area.attempt_ids.append(ORPHAN)
    caplog.clear()

    with caplog.at_level(logging.DEBUG, logger=GATE_LOGGER):
        gate.allows_claim()

    assert [record.getMessage() for record in caplog.records] == [OCCUPIED_MESSAGE]


# Teto ------------------------------------------------------------------------------------


def test_area_ocupada_segura_o_claim_ate_o_ultimo_instante_antes_do_teto() -> None:
    clock = FakeClock()
    gate = gate_over(FakeArea(ORPHAN), clock)
    gate.allows_claim()

    clock.now += 199.999

    assert gate.allows_claim() is False


def test_area_ocupada_libera_o_claim_no_teto() -> None:
    clock = FakeClock()
    gate = gate_over(FakeArea(ORPHAN), clock)
    gate.allows_claim()

    clock.now += 200.0

    assert gate.allows_claim() is True


def test_teto_zera_quando_a_area_esvazia() -> None:
    clock = FakeClock()
    area = FakeArea(ORPHAN)
    gate = gate_over(area, clock)
    gate.allows_claim()
    clock.now += 150.0
    area.attempt_ids.clear()
    gate.allows_claim()
    clock.now += 10.0
    area.attempt_ids.append(ORPHAN)
    gate.allows_claim()

    clock.now += 199.0

    assert gate.allows_claim() is False


def test_area_ilegivel_segura_o_claim_ate_o_ultimo_instante_antes_do_teto() -> None:
    clock = FakeClock()
    gate = gate_over(unreadable(), clock)
    gate.allows_claim()

    clock.now += 199.999

    assert gate.allows_claim() is False


def test_area_ilegivel_libera_o_claim_no_teto() -> None:
    clock = FakeClock()
    gate = gate_over(unreadable(), clock)
    gate.allows_claim()

    clock.now += 200.0

    assert gate.allows_claim() is True


def test_teto_conta_desde_a_primeira_vez_que_a_area_foi_vista_presa() -> None:
    # Ocupada e depois ilegível, sem esvaziar no meio: é a mesma espera.
    clock = FakeClock()
    area = FakeArea(ORPHAN)
    gate = gate_over(area, clock)
    gate.allows_claim()
    clock.now += 150.0
    area.error = PermissionError("sem acesso")
    gate.allows_claim()

    clock.now += 50.0

    assert gate.allows_claim() is True


def test_teto_atingido_registra_a_liberacao_uma_vez(caplog: pytest.LogCaptureFixture) -> None:
    clock = FakeClock()
    gate = gate_over(FakeArea(ORPHAN), clock)
    gate.allows_claim()
    clock.now += 200.0
    caplog.clear()

    with caplog.at_level(logging.DEBUG, logger=GATE_LOGGER):
        gate.allows_claim()
        clock.now += 2.0
        gate.allows_claim()

    [event] = caplog.records
    assert event.getMessage() == LIMIT_MESSAGE
    assert event.levelno == logging.WARNING
    assert event.__dict__["fields"] == {"held_s": 200.0, "limit_s": 200.0}


def test_area_ocupada_registra_aviso_com_as_tentativas(caplog: pytest.LogCaptureFixture) -> None:
    other = uuid.UUID("00000000-0000-4000-8000-000000000002")

    with caplog.at_level(logging.DEBUG, logger=GATE_LOGGER):
        gate_over(FakeArea(other, ORPHAN)).allows_claim()

    [event] = gate_records(caplog, OCCUPIED_MESSAGE)
    assert event.levelno == logging.WARNING
    assert event.__dict__["fields"] == {
        "attempts": 2,
        "attempt_ids": [str(ORPHAN), str(other)],
    }


def test_aviso_lista_no_maximo_cinco_tentativas(caplog: pytest.LogCaptureFixture) -> None:
    many = [uuid.UUID(int=number) for number in range(1, 8)]

    with caplog.at_level(logging.DEBUG, logger=GATE_LOGGER):
        gate_over(FakeArea(*many)).allows_claim()

    [event] = gate_records(caplog, OCCUPIED_MESSAGE)
    assert event.__dict__["fields"] == {
        "attempts": 7,
        "attempt_ids": [str(attempt_id) for attempt_id in many[:5]],
    }


def test_area_que_segue_ocupada_nao_repete_o_aviso(caplog: pytest.LogCaptureFixture) -> None:
    gate = gate_over(FakeArea(ORPHAN))

    with caplog.at_level(logging.DEBUG, logger=GATE_LOGGER):
        gate.allows_claim()
        gate.allows_claim()

    assert len(gate_records(caplog, OCCUPIED_MESSAGE)) == 1


def test_area_que_esvazia_registra_a_retomada(caplog: pytest.LogCaptureFixture) -> None:
    area = FakeArea(ORPHAN)
    gate = gate_over(area)
    gate.allows_claim()
    area.attempt_ids.clear()
    caplog.clear()

    with caplog.at_level(logging.DEBUG, logger=GATE_LOGGER):
        allowed = gate.allows_claim()

    assert allowed is True
    [event] = gate_records(caplog, FREED_MESSAGE)
    assert event.levelno == logging.INFO
    assert gate_records(caplog, OCCUPIED_MESSAGE) == []


def test_area_sempre_livre_nao_registra_nada(caplog: pytest.LogCaptureFixture) -> None:
    gate = gate_over(FakeArea())

    with caplog.at_level(logging.DEBUG, logger=GATE_LOGGER):
        gate.allows_claim()
        gate.allows_claim()

    assert caplog.records == []


def test_area_ocupada_de_novo_volta_a_avisar(caplog: pytest.LogCaptureFixture) -> None:
    area = FakeArea(ORPHAN)
    gate = gate_over(area)
    gate.allows_claim()
    area.attempt_ids.clear()
    gate.allows_claim()
    area.attempt_ids.append(ORPHAN)
    caplog.clear()

    with caplog.at_level(logging.DEBUG, logger=GATE_LOGGER):
        gate.allows_claim()

    assert len(gate_records(caplog, OCCUPIED_MESSAGE)) == 1


# Volta do worker ------------------------------------------------------------------------


class FakeCoordinator:
    def __init__(self) -> None:
        self.ran: list[ClaimedAttempt] = []

    def run(self, claimed: ClaimedAttempt) -> None:
        self.ran.append(claimed)


class FakeQueue:
    """Substitui `claim_next`: entrega uma tentativa por chamada e conta as chamadas."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, *_args: object, **_kwargs: object) -> ClaimedAttempt:
        self.calls += 1
        fields = {"run_id": uuid.uuid4(), "attempt_id": uuid.uuid4(), "generation": 1, "number": 1}
        return cast(ClaimedAttempt, SimpleNamespace(**fields))


@pytest.fixture
def queue(monkeypatch: pytest.MonkeyPatch) -> FakeQueue:
    fake = FakeQueue()
    monkeypatch.setattr(worker_entrypoint, "claim_next", fake)
    return fake


def fresh_pause() -> ClaimPause:
    """A pausa de um processo que acabou de subir: nenhuma falha vista, então inativa."""
    return ClaimPause(WINDOW)


def work_once(coordinator: FakeCoordinator, pause: ClaimPause, gate: WorkAreaGate) -> bool:
    return worker_entrypoint._work_once(
        cast(sessionmaker[Session], None),
        cast(FileStorage, None),
        timedelta(seconds=60),
        cast(Coordinator, coordinator),
        cast(RunFinishedReporter, SimpleNamespace(report=lambda _run_id: None)),
        pause,
        gate,
    )


def test_worker_reiniciado_com_tentativa_orfa_na_area_nao_reivindica(queue: FakeQueue) -> None:
    coordinator = FakeCoordinator()

    has_work = work_once(coordinator, fresh_pause(), gate_over(FakeArea(ORPHAN)))

    assert has_work is False
    assert queue.calls == 0
    assert coordinator.ran == []


def test_worker_com_area_livre_reivindica(queue: FakeQueue) -> None:
    coordinator = FakeCoordinator()

    has_work = work_once(coordinator, fresh_pause(), gate_over(FakeArea()))

    assert has_work is True
    assert queue.calls == 1
    assert len(coordinator.ran) == 1


def test_area_liberada_pela_manutencao_volta_a_reivindicar(queue: FakeQueue) -> None:
    area = FakeArea(ORPHAN)
    gate = gate_over(area)
    pause = fresh_pause()
    work_once(FakeCoordinator(), pause, gate)
    area.attempt_ids.clear()

    has_work = work_once(FakeCoordinator(), pause, gate)

    assert has_work is True
    assert queue.calls == 1


def test_area_ilegivel_nao_reivindica_nem_repete_traceback_a_cada_volta(
    queue: FakeQueue, caplog: pytest.LogCaptureFixture
) -> None:
    gate = gate_over(unreadable())
    pause = fresh_pause()

    with caplog.at_level(logging.DEBUG):
        first = work_once(FakeCoordinator(), pause, gate)
        second = work_once(FakeCoordinator(), pause, gate)

    assert (first, second) == (False, False)
    assert queue.calls == 0
    assert gate_records(caplog, TURN_FAILED_MESSAGE) == []
    assert [r.getMessage() for r in caplog.records if r.exc_info] == [UNREADABLE_MESSAGE]


def test_area_presa_alem_do_teto_volta_a_reivindicar(queue: FakeQueue) -> None:
    # O `start` então é recusado como antes e dispara o alerta `executor_start_failed`.
    clock = FakeClock()
    gate = gate_over(FakeArea(ORPHAN), clock)
    pause = fresh_pause()
    work_once(FakeCoordinator(), pause, gate)
    clock.now += HOLD_LIMIT.total_seconds()
    coordinator = FakeCoordinator()

    has_work = work_once(coordinator, pause, gate)

    assert has_work is True
    assert queue.calls == 1
    assert len(coordinator.ran) == 1


def test_durante_a_pausa_a_area_nao_e_consultada(queue: FakeQueue) -> None:
    # A pausa já decide a volta; a leitura da área seria I/O sem efeito.
    area = FakeArea()
    pause = ClaimPause(WINDOW)
    pause.begin()

    work_once(FakeCoordinator(), pause, gate_over(area))

    assert area.reads == 0


# Ligação no processo --------------------------------------------------------------------


def serve_gate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, area: FakeArea) -> WorkAreaGate:
    """O portão que o `serve` real monta, capturado na primeira volta (sem banco)."""
    shutdown = ShutdownFlag()
    gates: list[WorkAreaGate] = []
    listener = Mock()
    listener.return_value.__enter__ = Mock(return_value=listener)
    listener.return_value.__exit__ = Mock(return_value=None)
    monkeypatch.setattr(worker_entrypoint, "QueueListener", listener)

    def capture_gate(*args: object) -> bool:
        gates.append(cast(WorkAreaGate, args[-1]))
        shutdown.request()
        return True

    monkeypatch.setattr(worker_entrypoint, "_work_once", capture_gate)
    settings = Settings(environment="test", db_port=1, storage_root=tmp_path / "storage")

    worker_entrypoint.serve(settings, cast(Runner, area), shutdown)

    [gate] = gates
    return gate


def test_serve_confere_a_area_do_runner_do_processo(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    area = FakeArea(ORPHAN)
    gate = serve_gate(monkeypatch, tmp_path, area)

    assert gate.allows_claim() is False
    assert area.reads == 1


def test_portao_do_serve_mede_o_teto_no_relogio_monotonico(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Relógio de parede pula (NTP, ajuste manual) e encurtaria ou esticaria o teto.
    clock = FakeClock()
    monkeypatch.setattr(time, "monotonic", clock)
    gate = serve_gate(monkeypatch, tmp_path, FakeArea(ORPHAN))
    gate.allows_claim()

    clock.now += HOLD_LIMIT.total_seconds()

    assert gate.allows_claim() is True
