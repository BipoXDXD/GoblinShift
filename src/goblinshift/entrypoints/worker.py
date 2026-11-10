"""Processo `gs-worker`: claim, executor e publicação (docs/GoblinShiftPlan.md, 3.4).

Laço: tenta o claim; sem trabalho, espera um `NOTIFY goblinshift_runs` por até
`GS_WORKER_POLL_INTERVAL_S` (2 s), a garantia contra notificação perdida. Uma tentativa por vez.

SIGTERM e SIGINT param o laço de forma segura: o worker deixa de reivindicar e termina a
tentativa em andamento antes de sair. O prazo de parada do deploy (`stop_grace_period`, 30 s) é
menor que o teto de uma tentativa (10 min), então uma tentativa longa não termina a tempo: a
plataforma mata o processo (SIGKILL) e quem recupera é o lease vencido: a manutenção encontra
o executor pelo nome, confirma o término e reagenda. O trabalho recomeça do zero, e a Run pode
falhar se esgotar as tentativas ou o prazo.

Falha transitória do banco fora da supervisão (`record_start`, renovação no empacotamento,
desfecho) deixa a tentativa órfã, com a área de trabalho ocupada até a manutenção encerrá-la. O
worker então pausa os claims por `orphan_cleanup_window` (lease, intervalo da manutenção e
carência da parada), para a Run seguinte não receber `work_area_dirty` e gastar claims
(runs/worker/claim_pause.py).

Runner: em produção só o `executor_ctl` (container gVisor por tentativa, pelo serviço do host);
o processo recusa subir com o Runner local, que não tem sandbox (3.9, fatia 4).
"""

import logging
import signal
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta
from types import FrameType

from opentelemetry.trace import NoOpTracer, Tracer
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.files.storage import FileStorage
from goblinshift.platform.db import make_engine, make_session_factory
from goblinshift.platform.settings import ExecutorConfigError, MissingSecretError, Settings
from goblinshift.platform.telemetry import UserHasher, configure_logging, make_tracer_provider
from goblinshift.runs.finished import RunFinishedReporter
from goblinshift.runs.worker.claim import ClaimedAttempt, QueueListener, claim_next
from goblinshift.runs.worker.claim_pause import ClaimPause, orphan_cleanup_window
from goblinshift.runs.worker.coordinator import (
    TRANSIENT_DATABASE_ERRORS,
    Coordinator,
    CoordinatorConfig,
)
from goblinshift.runs.worker.executor_ctl_runner import ExecutorCtlRunner
from goblinshift.runs.worker.runner import STOP_GRACE, LocalRunner, Runner

logger = logging.getLogger("goblinshift.worker")

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_CONFIGURATION = 2


def main() -> int:
    configure_logging()
    settings = Settings()
    try:
        runner = build_runner(settings)
        settings.user_hash_key()
        settings.database_password()
    except (ExecutorConfigError, MissingSecretError) as error:
        logger.error("worker não sobe: %s", error)
        return EXIT_CONFIGURATION
    try:
        serve(settings, runner, shutdown_on_signals())
    except Exception:
        # Fronteira do processo, com o mesmo código de saída de antes: sem isto, o `sys.excepthook`
        # escreveria no stderr a mensagem crua do banco, com host, porta e usuário.
        logger.exception("worker encerrado por erro")
        return EXIT_FAILURE
    return EXIT_OK


def build_runner(settings: Settings) -> Runner:
    """Runner da configuração; `ExecutorConfigError` se ela não serve ao ambiente."""
    settings.executor_image_for_runs()
    if settings.runner == "executor_ctl":
        return ExecutorCtlRunner(
            settings.executor_ctl_socket, settings.work_root, work_group=settings.executor_group_id
        )
    return LocalRunner(
        settings.work_root, attempt_timeout=timedelta(seconds=settings.attempt_timeout_s)
    )


def serve(settings: Settings, runner: Runner, shutdown: threading.Event) -> None:
    engine = make_engine(settings)
    factory = make_session_factory(engine)
    storage = FileStorage(settings.storage_root)
    lease = timedelta(seconds=settings.worker_lease_s)
    config = CoordinatorConfig(
        lease=lease, attempt_timeout=timedelta(seconds=settings.attempt_timeout_s)
    )
    hasher = UserHasher(settings.user_hash_key())
    pause = claim_pause_for(settings)
    try:
        with (
            process_tracer(settings, "goblinshift-worker") as tracer,
            QueueListener(engine, settings.db_connect_timeout_s) as listener,
        ):
            # Um provider por processo: o span `attempt` e o `run.finished` saem pelo mesmo lote.
            finished = RunFinishedReporter(factory, hasher, tracer)
            coordinator = Coordinator(
                factory,
                storage,
                runner,
                config,
                on_finished=finished.report,
                tracer=tracer,
                user_hasher=hasher,
            )
            logger.info("worker pronto", extra={"fields": {"runner": settings.runner}})
            while not shutdown.is_set():
                if not _work_once(factory, storage, lease, coordinator, finished, pause):
                    logger.debug("fila vazia; aguardando NOTIFY")
                    listener.wait(settings.worker_poll_interval_s)
    finally:
        engine.dispose()
    logger.info("worker encerrado")


def claim_pause_for(settings: Settings) -> ClaimPause:
    """Pausa dos claims do processo, com os prazos que worker e manutenção leem da configuração."""
    window = orphan_cleanup_window(
        lease=timedelta(seconds=settings.worker_lease_s),
        maintenance_interval=timedelta(seconds=settings.maintenance_interval_s),
        stop_grace=STOP_GRACE,
    )
    return ClaimPause(window)


@contextmanager
def process_tracer(settings: Settings, default_service: str) -> Iterator[Tracer]:
    """Tracer dos eventos amplos do processo (3.7); ao sair, exporta o lote pendente.

    Sem endpoint OTLP (desenvolvimento e testes), um tracer que não grava.
    """
    provider = make_tracer_provider(settings, default_service)
    try:
        yield provider.get_tracer(__name__) if provider is not None else NoOpTracer()
    finally:
        if provider is not None:
            provider.shutdown()


@contextmanager
def run_finished_reporter(
    settings: Settings, factory: sessionmaker[Session], default_service: str
) -> Iterator[RunFinishedReporter]:
    """Emissor do `run.finished` (3.7) do processo, com o próprio `process_tracer`."""
    hasher = UserHasher(settings.user_hash_key())
    with process_tracer(settings, default_service) as tracer:
        yield RunFinishedReporter(factory, hasher, tracer)


def _work_once(
    factory: sessionmaker[Session],
    storage: FileStorage,
    lease: timedelta,
    coordinator: Coordinator,
    finished: RunFinishedReporter,
    pause: ClaimPause,
) -> bool:
    """Uma volta do laço; `True` se havia trabalho (vale tentar de novo sem esperar).

    Durante a pausa depois de uma tentativa órfã, a volta não reivindica e conta como fila vazia.
    """
    if pause.active:
        return False
    try:
        claimed = claim_next(factory, storage, lease, on_finished=finished.report)
        if claimed is None:
            return False
        _run_attempt(coordinator, claimed, pause)
    except Exception:
        # Fronteira do processo: uma falha (banco fora, disco) não derruba o worker. A tentativa
        # em curso fica com o lease, que vence, e a manutenção assume.
        logger.exception("volta do worker falhou")
        return False
    return True


def _run_attempt(coordinator: Coordinator, claimed: ClaimedAttempt, pause: ClaimPause) -> None:
    """Leva a tentativa até o fim; falha de banco que a deixa órfã pausa os claims e sobe."""
    try:
        coordinator.run(claimed)
    except TRANSIENT_DATABASE_ERRORS as error:
        pause.begin()
        # Só o tipo e o SQLSTATE: a mensagem do banco traz host, porta e usuário.
        sqlstate = getattr(getattr(error, "orig", None), "sqlstate", None)
        fields = {
            "run_id": str(claimed.run_id),
            "attempt_id": str(claimed.attempt_id),
            "error_type": type(error).__name__,
            "sqlstate": sqlstate,
            "pause_s": pause.window.total_seconds(),
        }
        logger.warning("claims pausados depois de falha de banco", extra={"fields": fields})
        raise


def shutdown_on_signals() -> threading.Event:
    shutdown = threading.Event()

    def request_shutdown(signum: int, _frame: FrameType | None) -> None:
        logger.info(
            "parada pedida; a tentativa em curso termina antes",
            extra={"fields": {"signal": signum}},
        )
        shutdown.set()

    signal.signal(signal.SIGTERM, request_shutdown)
    signal.signal(signal.SIGINT, request_shutdown)
    return shutdown


if __name__ == "__main__":
    raise SystemExit(main())
