"""Processo `gs-maintenance`: reconciliação e retenção em ciclos (docs/GoblinShiftPlan.md, 3.4).

Um ciclo a cada `GS_MAINTENANCE_INTERVAL_S` (30 s). Cada ciclo é uma unidade de trabalho com um
evento de log estruturado; a falha de um ciclo é registrada e o seguinte tenta de novo. Os lotes
usam `statement_timeout` de `GS_MAINTENANCE_STATEMENT_TIMEOUT_MS` (30 s, 3.2).

SIGTERM e SIGINT terminam o ciclo em andamento e saem.
"""

import logging
import signal
import threading
from types import FrameType

from goblinshift.entrypoints.worker import build_runner, run_finished_reporter
from goblinshift.files.storage import FileStorage
from goblinshift.platform.db import make_engine, make_session_factory
from goblinshift.platform.settings import ExecutorConfigError, MissingSecretError, Settings
from goblinshift.platform.telemetry import configure_logging
from goblinshift.runs.maintenance import Maintenance
from goblinshift.runs.worker.runner import Runner

logger = logging.getLogger("goblinshift.maintenance")

EXIT_OK = 0
EXIT_CONFIGURATION = 2


def main() -> int:
    configure_logging()
    settings = Settings()
    try:
        runner = build_runner(settings)
        settings.user_hash_key()
    except (ExecutorConfigError, MissingSecretError) as error:
        logger.error("manutenção não sobe: %s", error)
        return EXIT_CONFIGURATION
    shutdown = threading.Event()

    def request_shutdown(signum: int, _frame: FrameType | None) -> None:
        logger.info("parada pedida", extra={"fields": {"signal": signum}})
        shutdown.set()

    signal.signal(signal.SIGTERM, request_shutdown)
    signal.signal(signal.SIGINT, request_shutdown)
    serve(settings, runner, shutdown)
    return EXIT_OK


def serve(settings: Settings, runner: Runner, shutdown: threading.Event) -> None:
    batch_settings = settings.model_copy(
        update={"db_statement_timeout_ms": settings.maintenance_statement_timeout_ms}
    )
    engine = make_engine(batch_settings)
    factory = make_session_factory(engine)
    try:
        with run_finished_reporter(settings, factory, "goblinshift-maintenance") as finished:
            maintenance = Maintenance(
                factory, FileStorage(settings.storage_root), runner, on_finished=finished.report
            )
            logger.info("manutenção pronta")
            while not shutdown.is_set():
                try:
                    maintenance.run_cycle()
                except Exception:
                    # Fronteira do processo: o ciclo seguinte recomeça do banco, sem estado em
                    # memória.
                    logger.exception("ciclo de manutenção falhou")
                shutdown.wait(settings.maintenance_interval_s)
    finally:
        engine.dispose()
    logger.info("manutenção encerrada")


if __name__ == "__main__":
    raise SystemExit(main())
