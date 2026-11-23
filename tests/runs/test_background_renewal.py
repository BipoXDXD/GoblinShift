"""Lease renovado em paralelo enquanto o `start` do executor bloqueia (issue #442, 14b).

O `start` pode levar até o prazo do executor-ctl, bem mais que o lease. Sem renovação durante ele,
a manutenção veria o lease vencido e reagendaria a Run com o contêiner antigo ainda nascendo.
Sem banco: `renew_lease` é trocado no próprio coordenador; nenhum `sleep` serve de prova, só
`threading.Event` com teto como guarda de falha.
"""

import contextvars
import threading
import uuid
from datetime import timedelta

import pytest
from sqlalchemy.exc import OperationalError

from goblinshift.runs.lifecycle import Status
from goblinshift.runs.store import LostFencing
from goblinshift.runs.worker.background_renewal import BackgroundRenewal
from goblinshift.runs.worker.runner import ExecutorPhase, ExecutorStatus, RunnerError
from tests.runs.test_lease_renewal_failure import attempt_without_database, worker_without_database

# Lease de 3 s: renovação a cada 1 s, retentativa a cada 0,1 s. Os tetos de espera ficam em
# frações de `EVERY`, e só a guarda de falha passa disso.
LEASE = timedelta(seconds=3)
EVERY_S = LEASE.total_seconds() / 3
FAILURE_GUARD_S = 5.0


def test_start_lento_renova_o_lease_enquanto_bloqueia(monkeypatch: pytest.MonkeyPatch) -> None:
    renewals: list[Status] = []
    first, second = threading.Event(), threading.Event()
    seen_by_start: list[int] = []

    def renew(_claimed: object) -> Status:
        renewals.append(Status.RUNNING)
        (first if len(renewals) == 1 else second).set()
        return Status.RUNNING

    class SlowStart:
        def start(self, attempt_id: uuid.UUID, image: str) -> None:
            del attempt_id, image
            # A primeira renovação vem já, não depois de um intervalo inteiro.
            assert first.wait(EVERY_S / 2), "nenhuma renovação logo no começo do start"
            # A segunda prova a renovação periódica enquanto o start bloqueia.
            assert second.wait(FAILURE_GUARD_S), "renovação não se repetiu durante o start"
            seen_by_start.append(len(renewals))

    worker = worker_without_database(SlowStart(), lease=LEASE)
    monkeypatch.setattr(worker, "renew_lease", renew)

    worker.start_executor(attempt_without_database())

    assert seen_by_start[0] >= 2


class _RecordingRunner:
    """Runner cujo `start` só volta depois de uma renovação; grava as operações pedidas."""

    def __init__(self, renewed: threading.Event, operations: list[str]) -> None:
        self._renewed = renewed
        self._operations = operations

    def start(self, attempt_id: uuid.UUID, image: str) -> None:
        del attempt_id, image
        self._operations.append("start")
        assert self._renewed.wait(FAILURE_GUARD_S), "nenhuma renovação durante o start"

    def stop(self, attempt_id: uuid.UUID, grace: timedelta) -> ExecutorStatus:
        del attempt_id, grace
        self._operations.append("stop")
        return ExecutorStatus(ExecutorPhase.EXITED, exit_code=137)

    def remove(self, attempt_id: uuid.UUID) -> None:
        del attempt_id
        self._operations.append("remove")


def test_fencing_perdido_durante_o_start_abandona_sem_registrar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    renewed = threading.Event()
    operations: list[str] = []
    renewals: list[str] = []

    def renew(_claimed: object) -> Status:
        renewals.append("renew")
        renewed.set()
        raise LostFencing("outra tentativa assumiu")

    worker = worker_without_database(_RecordingRunner(renewed, operations), lease=LEASE)
    monkeypatch.setattr(worker, "renew_lease", renew)
    monkeypatch.setattr(worker, "prepare", lambda _claimed: None)
    monkeypatch.setattr(worker, "may_start", lambda _claimed: True)
    monkeypatch.setattr(worker, "record_start", lambda _claimed: operations.append("record_start"))

    applied = worker.run(attempt_without_database())

    # Sem fencing nada vai ao banco: o worker só para e remove o próprio executor.
    assert applied is None
    assert operations == ["start", "stop", "remove"]
    # A thread para no primeiro `LostFencing`, sem insistir.
    assert renewals == ["renew"]


def test_falha_transitoria_na_renovacao_repete_no_intervalo_curto(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    renewals: list[str] = []
    second = threading.Event()
    seen_by_start: list[int] = []

    def renew(_claimed: object) -> Status:
        renewals.append("renew")
        if len(renewals) == 1:
            raise OperationalError("stmt", {}, Exception("conexão caiu"))
        second.set()
        return Status.RUNNING

    class StartUntilRetried:
        def start(self, attempt_id: uuid.UUID, image: str) -> None:
            del attempt_id, image
            # A retentativa vem em `retry_every` (0,1 s), muito antes do intervalo inteiro (1 s).
            assert second.wait(EVERY_S / 2), "a renovação que falhou não foi repetida logo"
            seen_by_start.append(len(renewals))

    worker = worker_without_database(StartUntilRetried(), lease=LEASE)
    monkeypatch.setattr(worker, "renew_lease", renew)

    worker.start_executor(attempt_without_database())

    assert seen_by_start == [2]


_REQUEST_ID: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="vazio")


def test_renovacao_ve_o_contexto_de_quem_abriu_o_bloco() -> None:
    # Os avisos de "lease não renovado" saem da thread: sem o contexto do chamador, perderiam o
    # `trace_id` e o `span_id` da tentativa.
    seen: list[str] = []
    renewed = threading.Event()

    def renew() -> Status:
        seen.append(_REQUEST_ID.get())
        renewed.set()
        return Status.RUNNING

    token = _REQUEST_ID.set("tentativa-1")
    try:
        with BackgroundRenewal(renew, every=LEASE, retry_every=LEASE):
            assert renewed.wait(FAILURE_GUARD_S), "nenhuma renovação"
    finally:
        _REQUEST_ID.reset(token)

    assert seen == ["tentativa-1"]


def test_erro_do_corpo_tem_precedencia_sobre_o_da_renovacao() -> None:
    renewed = threading.Event()

    def renew() -> Status:
        renewed.set()
        raise LostFencing("outra tentativa assumiu")

    with (
        pytest.raises(RunnerError),
        BackgroundRenewal(renew, every=LEASE, retry_every=LEASE),
    ):
        assert renewed.wait(FAILURE_GUARD_S), "nenhuma renovação"
        raise RunnerError("start recusado")
