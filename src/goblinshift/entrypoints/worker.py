"""Processo `gs-worker`: claim, executor e publicação (docs/GoblinShiftPlan.md, 3.4).

Laço: tenta o claim; sem trabalho, espera um `NOTIFY goblinshift_runs` por até
`GS_WORKER_POLL_INTERVAL_S` (2 s), a garantia contra notificação perdida. Uma tentativa por vez.

SIGTERM e SIGINT param o laço de forma segura: o worker deixa de reivindicar e termina a
tentativa em andamento antes de sair, porque o deploy concede um prazo de parada maior que uma
tentativa (3.8). Se a plataforma matar o processo antes (SIGKILL), nada se perde: o lease vence,
e a manutenção encontra o executor pelo nome, confirma o término e reagenda.

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
from goblinshift.runs.worker.claim import QueueListener, claim_next
from goblinshift.runs.worker.coordinator import Coordinator, CoordinatorConfig
from goblinshift.runs.worker.executor_ctl_runner import ExecutorCtlRunner
from goblinshift.runs.worker.runner import LocalRunner, Runner

logger = logging.getLogger("goblinshift.worker")

EXIT_OK = 0
EXIT_CONFIGURATION = 2


def main() -> int:
    configure_logging()
    settings = Settings()
    try:
        runner = build_runner(settings)
        settings.user_hash_key()
    except (ExecutorConfigError, MissingSecretError) as error:
        logger.error("worker não sobe: %s", error)
        return EXIT_CONFIGURATION
    serve(settings, runner, shutdown_on_signals())
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
    try:
        with (
            run_finished_reporter(settings, factory, "goblinshift-worker") as finished,
            QueueListener(engine, settings.db_connect_timeout_s) as listener,
        ):
            coordinator = Coordinator(factory, storage, runner, config, on_finished=finished.report)
            logger.info("worker pronto", extra={"fields": {"runner": settings.runner}})
            while not shutdown.is_set():
                if not _work_once(factory, storage, lease, coordinator, finished):
                    logger.debug("fila vazia; aguardando NOTIFY")
                    listener.wait(settings.worker_poll_interval_s)
    finally:
        engine.dispose()
    logger.info("worker encerrado")


@contextmanager
def run_finished_reporter(
    settings: Settings, factory: sessionmaker[Session], default_service: str
) -> Iterator[RunFinishedReporter]:
    """Emissor do `run.finished` (3.7) do processo; ao sair, exporta o lote pendente.

    Sem endpoint OTLP (desenvolvimento e testes), o evento vai para um tracer que não grava.
    """
    hasher = UserHasher(settings.user_hash_key())
    provider = make_tracer_provider(settings, default_service)
    tracer: Tracer = provider.get_tracer(__name__) if provider is not None else NoOpTracer()
    try:
        yield RunFinishedReporter(factory, hasher, tracer)
    finally:
        if provider is not None:
            provider.shutdown()


def _work_once(
    factory: sessionmaker[Session],
    storage: FileStorage,
    lease: timedelta,
    coordinator: Coordinator,
    finished: RunFinishedReporter,
) -> bool:
    """Uma volta do laço; `True` se havia trabalho (vale tentar de novo sem esperar)."""
    try:
        claimed = claim_next(factory, storage, lease, on_finished=finished.report)
        if claimed is None:
            return False
        coordinator.run(claimed)
    except Exception:
        # Fronteira do processo: uma falha (banco fora, disco) não derruba o worker. A tentativa
        # em curso fica com o lease, que vence, e a manutenção assume.
        logger.exception("volta do worker falhou")
        return False
    return True


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
