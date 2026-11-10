"""Parada pedida no meio do lote de um passo da manutenção (lacuna L3).

Sem banco: as leituras e a decisão são trocadas na instância, e o Runner é falso. Os dois passos
que chamam o Runner item a item (`reconcile_expired_leases` e `sweep_work_areas`) podem esperar
minutos por tentativa no `executor-ctl`; com o SIGTERM do deploy no meio do lote, a manutenção
termina o item em curso, não começa o seguinte e fecha a volta com o evento do ciclo. A parada só
é conferida entre itens: o item em curso termina inteiro e, sozinho, pode passar da carência da
plataforma (`stop` e `remove` somam até minutos); nesse caso o SIGKILL ainda leva o evento. Isso
depende do `stop_grace_period` do compose, fora daqui. A mesma regra contra o Postgres e o
`executor-ctl` falso está em tests/runs/test_maintenance_executor_ctl.py.
"""

import logging
import signal
import uuid
from datetime import timedelta
from pathlib import Path
from typing import cast

import pytest
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.entrypoints.shutdown import ShutdownFlag
from goblinshift.files.storage import FileStorage
from goblinshift.runs.maintenance import (
    STEPS_FAILED,
    STEPS_SKIPPED,
    Maintenance,
    _ExpiredAttempt,
)
from goblinshift.runs.store import Applied
from goblinshift.runs.worker.runner import ExecutorPhase, ExecutorStatus

MAINTENANCE_LOGGER = "goblinshift.runs.maintenance"
CYCLE_MESSAGE = "ciclo de manutenção"
ITEMS_LEFT_MESSAGE = "parada pedida; itens restantes do passo pulados"
BATCH = 3
# Passos do ciclo depois da reconciliação de leases.
STEPS_AFTER_RECONCILE = 9


class RecordingRunner:
    """Runner falso: executores vivos até o `stop`, e um gancho por operação."""

    def __init__(self, work: list[uuid.UUID], shutdown: ShutdownFlag, signal_on: str) -> None:
        self.calls: list[tuple[str, uuid.UUID]] = []
        self._work = work
        self._shutdown = shutdown
        self._signal_on = signal_on

    def _record(self, operation: str, attempt_id: uuid.UUID) -> None:
        self.calls.append((operation, attempt_id))
        if operation == self._signal_on:
            self._shutdown.request(signal.SIGTERM)

    def list_work(self) -> list[uuid.UUID]:
        return list(self._work)

    def status(self, attempt_id: uuid.UUID) -> ExecutorStatus:
        self._record("status", attempt_id)
        return ExecutorStatus(ExecutorPhase.RUNNING)

    def stop(self, attempt_id: uuid.UUID, grace: timedelta) -> ExecutorStatus:
        del grace
        self._record("stop", attempt_id)
        return ExecutorStatus(ExecutorPhase.EXITED, exit_code=143)

    def remove(self, attempt_id: uuid.UUID) -> None:
        self._record("remove", attempt_id)

    def work_dir(self, attempt_id: uuid.UUID) -> Path:
        raise NotImplementedError

    def create_work_dir(self, attempt_id: uuid.UUID) -> Path:
        raise NotImplementedError

    def start(self, attempt_id: uuid.UUID, image: str) -> ExecutorStatus:
        raise NotImplementedError


def _janitor(runner: RecordingRunner) -> Maintenance:
    return Maintenance(cast(sessionmaker[Session], None), cast(FileStorage, None), runner)


def _no_op_steps(janitor: Maintenance, monkeypatch: pytest.MonkeyPatch, *names: str) -> None:
    for name in names:
        monkeypatch.setattr(janitor, name, lambda **_: {})


# Reconciliação de leases -------------------------------------------------------------------


@pytest.fixture
def expired() -> list[_ExpiredAttempt]:
    return [_ExpiredAttempt(uuid.uuid7(), 1, uuid.uuid7()) for _ in range(BATCH)]


def _reconciling(
    monkeypatch: pytest.MonkeyPatch,
    expired: list[_ExpiredAttempt],
    runner: RecordingRunner,
    decided: list[uuid.UUID],
) -> Maintenance:
    janitor = _janitor(runner)
    monkeypatch.setattr(janitor, "_expired_attempts", lambda: list(expired))
    monkeypatch.setattr(janitor, "_lease_still_expired", lambda _expired: True)

    def decide(run_id: uuid.UUID, _event_for: object) -> Applied:
        decided.append(run_id)
        return cast(Applied, object())

    monkeypatch.setattr(janitor, "_decide", decide)
    return janitor


def test_parada_no_stop_termina_a_tentativa_em_curso_e_nao_comeca_a_seguinte(
    monkeypatch: pytest.MonkeyPatch,
    expired: list[_ExpiredAttempt],
    caplog: pytest.LogCaptureFixture,
) -> None:
    shutdown = ShutdownFlag()
    runner = RecordingRunner([], shutdown, signal_on="stop")
    decided: list[uuid.UUID] = []
    janitor = _reconciling(monkeypatch, expired, runner, decided)
    first = expired[0]

    with caplog.at_level(logging.INFO, logger=MAINTENANCE_LOGGER):
        counts = janitor.run_cycle(should_stop=shutdown.is_set)

    # A tentativa em curso vai até o fim: parada, decidida e removida.
    assert runner.calls == [
        ("status", first.attempt_id),
        ("stop", first.attempt_id),
        ("remove", first.attempt_id),
    ]
    assert decided == [first.run_id]
    assert counts["leases_reconciled"] == 1
    assert (counts[STEPS_FAILED], counts[STEPS_SKIPPED]) == (0, STEPS_AFTER_RECONCILE)
    # A volta fecha com o evento do ciclo, e o log diz quantos itens ficaram.
    [cycle] = [r for r in caplog.records if r.getMessage() == CYCLE_MESSAGE]
    assert cycle.fields["leases_reconciled"] == 1  # type: ignore[attr-defined]
    [left] = [r for r in caplog.records if r.getMessage() == ITEMS_LEFT_MESSAGE]
    assert left.fields == {  # type: ignore[attr-defined]
        "step": "reconcile_expired_leases",
        "items_left": BATCH - 1,
    }


def test_parada_no_remove_da_ultima_tentativa_nao_deixa_itens_para_tras(
    monkeypatch: pytest.MonkeyPatch,
    expired: list[_ExpiredAttempt],
    caplog: pytest.LogCaptureFixture,
) -> None:
    shutdown = ShutdownFlag()
    runner = RecordingRunner([], shutdown, signal_on="none")
    decided: list[uuid.UUID] = []
    janitor = _reconciling(monkeypatch, expired, runner, decided)
    last = expired[-1]
    original_remove = runner.remove

    def remove_then_signal(attempt_id: uuid.UUID) -> None:
        original_remove(attempt_id)
        if attempt_id == last.attempt_id:
            shutdown.request(signal.SIGTERM)

    monkeypatch.setattr(runner, "remove", remove_then_signal)

    with caplog.at_level(logging.INFO, logger=MAINTENANCE_LOGGER):
        reconciled = janitor.reconcile_expired_leases(should_stop=shutdown.is_set)

    assert reconciled == BATCH
    assert decided == [item.run_id for item in expired]
    assert ITEMS_LEFT_MESSAGE not in caplog.text


def test_sem_parada_o_lote_inteiro_e_reconciliado(
    monkeypatch: pytest.MonkeyPatch, expired: list[_ExpiredAttempt]
) -> None:
    shutdown = ShutdownFlag()
    runner = RecordingRunner([], shutdown, signal_on="none")
    decided: list[uuid.UUID] = []
    janitor = _reconciling(monkeypatch, expired, runner, decided)

    reconciled = janitor.reconcile_expired_leases(should_stop=shutdown.is_set)

    assert reconciled == BATCH
    assert [op for op, _ in runner.calls] == ["status", "stop", "remove"] * BATCH


def test_parada_antes_do_lote_nao_toca_tentativa_nenhuma(
    monkeypatch: pytest.MonkeyPatch, expired: list[_ExpiredAttempt]
) -> None:
    shutdown = ShutdownFlag()
    shutdown.request(signal.SIGTERM)
    runner = RecordingRunner([], shutdown, signal_on="none")
    decided: list[uuid.UUID] = []
    janitor = _reconciling(monkeypatch, expired, runner, decided)

    reconciled = janitor.reconcile_expired_leases(should_stop=shutdown.is_set)

    assert (reconciled, runner.calls, decided) == (0, [], [])


# Limpeza das áreas de trabalho -------------------------------------------------------------


def test_parada_na_limpeza_remove_a_area_em_curso_e_mantem_as_seguintes(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    areas = [uuid.uuid7() for _ in range(BATCH)]
    shutdown = ShutdownFlag()
    runner = RecordingRunner(areas, shutdown, signal_on="status")
    janitor = _janitor(runner)
    monkeypatch.setattr(janitor, "_live_attempts", lambda _present: set())
    _no_op_steps(
        janitor,
        monkeypatch,
        "reconcile_expired_leases",
        "expire_queue",
        "apply_deadlines",
        "release_orphan_holds",
        "discard_stale_candidates",
    )
    first = areas[0]

    with caplog.at_level(logging.INFO, logger=MAINTENANCE_LOGGER):
        counts = janitor.run_cycle(should_stop=shutdown.is_set)

    assert runner.calls == [("status", first), ("stop", first), ("remove", first)]
    assert counts["work_areas_removed"] == 1
    [left] = [r for r in caplog.records if r.getMessage() == ITEMS_LEFT_MESSAGE]
    assert left.fields == {  # type: ignore[attr-defined]
        "step": "sweep_work_areas",
        "items_left": BATCH - 1,
    }
    [cycle] = [r for r in caplog.records if r.getMessage() == CYCLE_MESSAGE]
    assert cycle.fields["work_areas_removed"] == 1  # type: ignore[attr-defined]


def test_parada_na_limpeza_nao_conta_as_areas_vivas_como_restantes(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    live, ended, other = (uuid.uuid7() for _ in range(BATCH))
    shutdown = ShutdownFlag()
    runner = RecordingRunner([live, ended, other], shutdown, signal_on="remove")
    janitor = _janitor(runner)
    monkeypatch.setattr(janitor, "_live_attempts", lambda _present: {live})

    with caplog.at_level(logging.INFO, logger=MAINTENANCE_LOGGER):
        removed = janitor.sweep_work_areas(should_stop=shutdown.is_set)

    assert removed == 1
    assert runner.calls == [("status", ended), ("stop", ended), ("remove", ended)]
    [left] = [r for r in caplog.records if r.getMessage() == ITEMS_LEFT_MESSAGE]
    assert left.fields == {"step": "sweep_work_areas", "items_left": 1}  # type: ignore[attr-defined]
