"""Pausa dos claims depois de falha de banco que pode deixar tentativa órfã (auditoria D3).

Sem banco: o relógio é injetado, o claim e o coordenador são falsos. O cenário com Postgres e o
`executor-ctl` falso está em `test_claim_pause_after_db_failure.py`.
"""

import logging
import uuid
from datetime import timedelta
from types import SimpleNamespace
from typing import cast

import psycopg
import pytest
from psycopg import errors as psycopg_errors
from sqlalchemy.exc import DBAPIError, OperationalError, ProgrammingError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.entrypoints import worker as worker_entrypoint
from goblinshift.files.storage import FileStorage
from goblinshift.platform.settings import Settings
from goblinshift.runs.finished import RunFinishedReporter
from goblinshift.runs.worker.claim import ClaimedAttempt
from goblinshift.runs.worker.claim_pause import ClaimPause, orphan_cleanup_window
from goblinshift.runs.worker.coordinator import Coordinator

WINDOW = timedelta(seconds=100)
WORKER_LOGGER = "goblinshift.worker"
PAUSED_MESSAGE = "claims pausados depois de falha de banco"
TURN_FAILED_MESSAGE = "volta do worker falhou"
LOCK_NOT_AVAILABLE = "55P03"


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def operational_error(sqlstate: str = LOCK_NOT_AVAILABLE) -> Exception:
    driver_error = psycopg_errors.lookup(sqlstate)()
    return DBAPIError.instance("stmt", {}, driver_error, psycopg.Error)


# Janela ---------------------------------------------------------------------------------


def test_janela_cobre_lease_ciclo_da_manutencao_e_carencia_da_parada() -> None:
    window = orphan_cleanup_window(
        lease=timedelta(seconds=60),
        maintenance_interval=timedelta(seconds=30),
        stop_grace=timedelta(seconds=10),
    )

    assert window == timedelta(seconds=100)


def test_pausa_do_processo_usa_os_prazos_de_producao_por_padrao() -> None:
    pause = worker_entrypoint.claim_pause_for(Settings.model_validate({"environment": "test"}))

    assert pause.window == timedelta(seconds=100)


def test_pausa_do_processo_soma_lease_ciclo_da_manutencao_e_carencia_da_parada() -> None:
    settings = Settings.model_validate(
        {"environment": "test", "worker_lease_s": 2, "maintenance_interval_s": 3}
    )

    pause = worker_entrypoint.claim_pause_for(settings)

    assert pause.window == timedelta(seconds=15)


def test_sem_falha_nao_ha_pausa() -> None:
    pause = ClaimPause(WINDOW, clock=FakeClock())

    assert pause.active is False


def test_pausa_vale_ate_o_ultimo_instante_antes_do_fim_da_janela() -> None:
    clock = FakeClock()
    pause = ClaimPause(WINDOW, clock=clock)
    pause.begin()

    clock.now += 99.999

    assert pause.active is True


def test_pausa_termina_exatamente_no_fim_da_janela() -> None:
    clock = FakeClock()
    pause = ClaimPause(WINDOW, clock=clock)
    pause.begin()

    clock.now += 100.0

    assert pause.active is False


def test_nova_falha_durante_a_pausa_reinicia_a_janela() -> None:
    clock = FakeClock()
    pause = ClaimPause(WINDOW, clock=clock)
    pause.begin()
    clock.now += 60.0
    pause.begin()

    clock.now += 99.0

    assert pause.active is True


def test_janela_negativa_e_recusada() -> None:
    with pytest.raises(ValueError, match="janela"):
        ClaimPause(timedelta(seconds=-1))


# Volta do worker ------------------------------------------------------------------------


class FakeCoordinator:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.ran: list[ClaimedAttempt] = []

    def run(self, claimed: ClaimedAttempt) -> None:
        self.ran.append(claimed)
        if self.error is not None:
            raise self.error


class FakeQueue:
    """Substitui `claim_next`: entrega uma tentativa por chamada e conta as chamadas."""

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls = 0

    def __call__(self, *_args: object, **_kwargs: object) -> ClaimedAttempt:
        self.calls += 1
        if self.error is not None:
            raise self.error
        fields = {"run_id": uuid.uuid4(), "attempt_id": uuid.uuid4(), "generation": 1, "number": 1}
        return cast(ClaimedAttempt, SimpleNamespace(**fields))


@pytest.fixture
def queue(monkeypatch: pytest.MonkeyPatch) -> FakeQueue:
    fake = FakeQueue()
    monkeypatch.setattr(worker_entrypoint, "claim_next", fake)
    return fake


def work_once(coordinator: FakeCoordinator, pause: ClaimPause) -> bool:
    return worker_entrypoint._work_once(
        cast(sessionmaker[Session], None),
        cast(FileStorage, None),
        timedelta(seconds=60),
        cast(Coordinator, coordinator),
        cast(RunFinishedReporter, SimpleNamespace(report=lambda _run_id: None)),
        pause,
    )


def paused_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [record for record in caplog.records if record.getMessage() == PAUSED_MESSAGE]


@pytest.mark.parametrize(
    "error",
    [
        pytest.param(operational_error(), id="lock-timeout"),
        pytest.param(operational_error("57014"), id="statement-timeout"),
        pytest.param(OperationalError("stmt", {}, Exception("x")), id="conexao-caida"),
        pytest.param(PoolTimeoutError("pool esgotado"), id="pool-esgotado"),
    ],
)
def test_falha_de_banco_na_tentativa_pausa_os_claims(queue: FakeQueue, error: Exception) -> None:
    clock = FakeClock()
    pause = ClaimPause(WINDOW, clock=clock)

    has_work = work_once(FakeCoordinator(error), pause)

    assert has_work is False
    assert pause.active is True


def test_durante_a_pausa_o_worker_nao_reivindica(queue: FakeQueue) -> None:
    clock = FakeClock()
    pause = ClaimPause(WINDOW, clock=clock)
    work_once(FakeCoordinator(operational_error()), pause)
    clock.now += 99.999
    coordinator = FakeCoordinator()

    has_work = work_once(coordinator, pause)

    assert has_work is False
    assert queue.calls == 1
    assert coordinator.ran == []


def test_no_fim_da_janela_o_worker_volta_a_reivindicar(queue: FakeQueue) -> None:
    clock = FakeClock()
    pause = ClaimPause(WINDOW, clock=clock)
    work_once(FakeCoordinator(operational_error()), pause)
    clock.now += 100.0
    coordinator = FakeCoordinator()

    has_work = work_once(coordinator, pause)

    assert has_work is True
    assert queue.calls == 2
    assert len(coordinator.ran) == 1


def test_tentativa_concluida_nao_pausa(queue: FakeQueue) -> None:
    pause = ClaimPause(WINDOW, clock=FakeClock())

    has_work = work_once(FakeCoordinator(), pause)

    assert has_work is True
    assert pause.active is False


def test_falha_de_banco_no_proprio_claim_nao_pausa(monkeypatch: pytest.MonkeyPatch) -> None:
    # O claim é uma transação só: se ela falhou, não há tentativa deste worker no banco.
    monkeypatch.setattr(worker_entrypoint, "claim_next", FakeQueue(operational_error()))
    pause = ClaimPause(WINDOW, clock=FakeClock())

    has_work = work_once(FakeCoordinator(), pause)

    assert has_work is False
    assert pause.active is False


def test_erro_que_nao_e_falha_transitoria_do_banco_nao_pausa(queue: FakeQueue) -> None:
    pause = ClaimPause(WINDOW, clock=FakeClock())

    work_once(FakeCoordinator(ProgrammingError("stmt", {}, Exception("x"))), pause)

    assert pause.active is False


def test_pausa_nao_engole_o_erro_que_a_causou(
    queue: FakeQueue, caplog: pytest.LogCaptureFixture
) -> None:
    pause = ClaimPause(WINDOW, clock=FakeClock())

    with caplog.at_level(logging.INFO, logger=WORKER_LOGGER):
        work_once(FakeCoordinator(operational_error()), pause)

    [failed] = [r for r in caplog.records if r.getMessage() == TURN_FAILED_MESSAGE]
    assert failed.levelno == logging.ERROR
    assert failed.exc_info is not None
    assert isinstance(failed.exc_info[1], OperationalError)


def test_pausa_registra_evento_com_ids_tipo_sqlstate_e_duracao(
    queue: FakeQueue, caplog: pytest.LogCaptureFixture
) -> None:
    pause = ClaimPause(WINDOW, clock=FakeClock())
    coordinator = FakeCoordinator(operational_error())

    with caplog.at_level(logging.INFO, logger=WORKER_LOGGER):
        work_once(coordinator, pause)

    [claimed] = coordinator.ran
    [event] = paused_records(caplog)
    assert event.levelno == logging.WARNING
    assert event.__dict__["fields"] == {
        "run_id": str(claimed.run_id),
        "attempt_id": str(claimed.attempt_id),
        "error_type": "OperationalError",
        "sqlstate": LOCK_NOT_AVAILABLE,
        "pause_s": 100.0,
    }


def test_sem_falha_nao_ha_evento_de_pausa(
    queue: FakeQueue, caplog: pytest.LogCaptureFixture
) -> None:
    pause = ClaimPause(WINDOW, clock=FakeClock())

    with caplog.at_level(logging.INFO, logger=WORKER_LOGGER):
        work_once(FakeCoordinator(), pause)

    assert paused_records(caplog) == []
