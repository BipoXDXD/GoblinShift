"""Lease renovado em paralelo enquanto o `start` do executor bloqueia (issue #442, 14b).

O `start` pode levar até o prazo do executor-ctl, bem mais que o lease. Sem renovação durante ele,
a manutenção veria o lease vencido e reagendaria a Run com o contêiner antigo ainda nascendo.
Sem banco: `renew_lease` é trocado no próprio coordenador; nenhum `sleep` serve de prova, só
`threading.Event` com teto como guarda de falha.
"""

import contextvars
import math
import threading
import uuid
from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import OperationalError

from goblinshift.runs.lifecycle import Status
from goblinshift.runs.store import LostFencing
from goblinshift.runs.worker import coordinator
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
    assert not _renewal_threads_alive()


def _renewal_threads_alive() -> list[threading.Thread]:
    """Threads de renovação vivas: a saída do bloco espera a sua terminar (`join`)."""
    return [thread for thread in threading.enumerate() if thread.name == "lease-renewal"]


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
    body_failing = threading.Event()

    def renew() -> Status:
        renewed.set()
        # Ainda renovando quando o corpo levanta: a saída do bloco precisa esperar esta thread.
        assert body_failing.wait(FAILURE_GUARD_S), "o corpo não levantou"
        raise LostFencing("outra tentativa assumiu")

    with (
        pytest.raises(RunnerError),
        BackgroundRenewal(renew, every=LEASE, retry_every=LEASE),
    ):
        assert renewed.wait(FAILURE_GUARD_S), "nenhuma renovação"
        body_failing.set()
        raise RunnerError("start recusado")

    assert not _renewal_threads_alive()


# --- `stop` durante a supervisão (issue #442, 14c) ------------------------------------------------

RUNNING = ExecutorStatus(ExecutorPhase.RUNNING)
# 128 + SIGKILL: o código de quem parou pelo `stop` depois da carência.
STOPPED = ExecutorStatus(ExecutorPhase.EXITED, exit_code=137)


class _SupervisedRunner:
    """Executor que roda até o `stop`; grava `status` e `stop` na ordem em que são pedidos.

    `stop_results` é a resposta de cada `stop`, na ordem; um `stop` além da lista reprova o teste.
    """

    def __init__(self, calls: list[str], *, stop_results: list[ExecutorStatus]) -> None:
        self._calls = calls
        self._stop_results = iter(stop_results)

    def status(self, attempt_id: uuid.UUID) -> ExecutorStatus:
        del attempt_id
        self._calls.append("status")
        return RUNNING

    def stop(self, attempt_id: uuid.UUID, grace: timedelta) -> ExecutorStatus:
        del attempt_id, grace
        self._calls.append("stop")
        for result in self._stop_results:
            return result
        pytest.fail("stop pedido além do previsto")


def test_uma_volta_do_supervise_faz_no_maximo_um_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    # Run em `stopping` e teto vencido na mesma volta: dois `stop` seguidos somariam o pior caso do
    # executor-ctl duas vezes, e o segundo ainda abriria uma parada concorrente à primeira.
    calls: list[str] = []
    runner = _SupervisedRunner(calls, stop_results=[RUNNING, STOPPED])
    worker = worker_without_database(runner, attempt_timeout=timedelta(0))
    monkeypatch.setattr(worker, "renew_lease", lambda _claimed: Status.STOPPING)

    status = worker.supervise(attempt_without_database())

    assert status.phase is ExecutorPhase.EXITED
    assert calls.count("stop") == 2
    assert "stop,stop" not in ",".join(calls)


def test_stop_renova_o_lease_enquanto_bloqueia(monkeypatch: pytest.MonkeyPatch) -> None:
    # O `stop` do executor-ctl chega a 160 s mais a carência, contra 60 s de lease. A fase "stop em
    # curso" é marcada pelo próprio `stop`: a renovação de entrada pode cair antes ou depois dele, e
    # só a segunda renovação dentro do `stop` prova que ela se repete enquanto ele bloqueia.
    stopping = threading.Event()
    renewed_during_stop = threading.Event()
    during_stop: list[Status] = []

    def renew(_claimed: object) -> Status:
        if stopping.is_set():
            during_stop.append(Status.STOPPING)
            if len(during_stop) >= 2:
                renewed_during_stop.set()
        return Status.STOPPING

    class SlowStop:
        def status(self, attempt_id: uuid.UUID) -> ExecutorStatus:
            del attempt_id
            return RUNNING

        def stop(self, attempt_id: uuid.UUID, grace: timedelta) -> ExecutorStatus:
            del attempt_id, grace
            stopping.set()
            assert renewed_during_stop.wait(FAILURE_GUARD_S), "lease não renovado durante o stop"
            return STOPPED

    worker = worker_without_database(SlowStop(), lease=LEASE)
    monkeypatch.setattr(worker, "renew_lease", renew)

    status = worker.supervise(attempt_without_database())

    assert status == STOPPED
    assert len(during_stop) >= 2


def test_teto_com_executor_ausente_nao_marca_estouro(monkeypatch: pytest.MonkeyPatch) -> None:
    # O `stop` de um contêiner que nunca iniciou (`created`) remove e devolve `absent`. Marcar o
    # teto aqui viraria RESOURCE_LIMIT_EXCEEDED, culpa do usuário e sem nova tentativa, para uma
    # falha de infraestrutura: o desfecho fica para o `finish`, sem código de saída (transitória).
    calls: list[str] = []
    absent = ExecutorStatus(ExecutorPhase.ABSENT)
    runner = _SupervisedRunner(calls, stop_results=[absent])
    worker = worker_without_database(runner, attempt_timeout=timedelta(0))
    monkeypatch.setattr(worker, "renew_lease", lambda _claimed: Status.RUNNING)

    status = worker.supervise(attempt_without_database())

    assert status == absent
    assert not status.timed_out


def test_teto_com_stop_falhando_repete_no_intervalo_curto(monkeypatch: pytest.MonkeyPatch) -> None:
    # Com o executor-ctl fora, o `stop` falha na hora. Repeti-lo a cada `status_poll` abriria uma
    # thread de renovação por volta, cada uma com uma transação no banco: o teto espera
    # `renew_retry_every` entre um `stop` falho e o próximo. Relógio falso: o `sleep` o avança.
    clock = SimpleNamespace(now=0.0)

    def advance(seconds: float) -> None:
        clock.now += seconds

    monkeypatch.setattr(
        coordinator, "time", SimpleNamespace(monotonic=lambda: clock.now, sleep=advance)
    )
    failures = 20
    stops: list[float] = []

    class StopFailingFirst:
        def status(self, attempt_id: uuid.UUID) -> ExecutorStatus:
            del attempt_id
            return RUNNING

        def stop(self, attempt_id: uuid.UUID, grace: timedelta) -> ExecutorStatus:
            del attempt_id, grace
            stops.append(clock.now)
            if len(stops) <= failures:
                raise RunnerError("executor-ctl indisponível")
            return STOPPED

    worker = worker_without_database(StopFailingFirst(), lease=LEASE, attempt_timeout=timedelta(0))
    monkeypatch.setattr(worker, "renew_lease", lambda _claimed: Status.RUNNING)
    retry_every = EVERY_S / 10

    status = worker.supervise(attempt_without_database())

    assert status.timed_out
    assert len(stops) == failures + 1
    # Tolerância só para o arredondamento da soma de `status_poll` no relógio falso.
    assert len(stops) <= math.floor(clock.now / retry_every + 1e-6) + 1


def test_fencing_perdido_durante_o_stop_abandona_sem_registrar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # O `LostFencing` da thread que renova durante o `stop` sai do `supervise`: o worker abandona,
    # para e remove o próprio executor sem levar desfecho ao banco.
    stopping = threading.Event()
    lost_in_thread = threading.Event()
    operations: list[str] = []

    def renew(_claimed: object) -> Status:
        if threading.current_thread().name == "lease-renewal" and stopping.is_set():
            lost_in_thread.set()
            raise LostFencing("outra tentativa assumiu")
        return Status.STOPPING

    class StopAfterLostFencing:
        def __init__(self) -> None:
            self._stops = 0

        def start(self, attempt_id: uuid.UUID, image: str) -> None:
            del attempt_id, image
            operations.append("start")

        def status(self, attempt_id: uuid.UUID) -> ExecutorStatus:
            del attempt_id
            return RUNNING

        def stop(self, attempt_id: uuid.UUID, grace: timedelta) -> ExecutorStatus:
            del attempt_id, grace
            operations.append("stop")
            self._stops += 1
            if self._stops == 1:
                # Só o primeiro `stop` (o da supervisão) renova; o do abandono vem sem fencing.
                stopping.set()
                assert lost_in_thread.wait(FAILURE_GUARD_S), "fencing não perdido durante o stop"
            return STOPPED

        def remove(self, attempt_id: uuid.UUID) -> None:
            del attempt_id
            operations.append("remove")

    def no_outcome(*_args: object, **_kwargs: object) -> None:
        pytest.fail("desfecho registrado sem fencing")

    worker = worker_without_database(StopAfterLostFencing())
    monkeypatch.setattr(worker, "renew_lease", renew)
    monkeypatch.setattr(worker, "prepare", lambda _claimed: None)
    monkeypatch.setattr(worker, "may_start", lambda _claimed: True)
    monkeypatch.setattr(worker, "record_start", lambda _claimed: None)
    monkeypatch.setattr(worker, "finish", no_outcome)
    monkeypatch.setattr(worker, "_conclude", no_outcome)

    applied = worker.run(attempt_without_database())

    assert applied is None
    assert operations == ["start", "stop", "stop", "remove"]
