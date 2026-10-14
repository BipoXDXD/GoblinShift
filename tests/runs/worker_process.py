"""Worker de verdade, num processo próprio, com o executor de teste.

    python tests/runs/worker_process.py <modo do fake_executor…>

Lê as mesmas variáveis GS_* do `gs-worker` e roda o mesmo `serve`; só o comando do executor
muda, para abrir a janela em que o teste mata ou pausa o processo.
"""

import sys
from datetime import timedelta
from pathlib import Path

from goblinshift.entrypoints.worker import serve, shutdown_on_signals
from goblinshift.platform.settings import Settings
from goblinshift.platform.telemetry import configure_logging
from goblinshift.runs.worker.runner import LocalRunner

FAKE_EXECUTOR = str(Path(__file__).with_name("fake_executor.py"))

if __name__ == "__main__":
    configure_logging()
    settings = Settings()
    runner = LocalRunner(
        settings.work_root,
        attempt_timeout=timedelta(seconds=settings.attempt_timeout_s),
        command=(sys.executable, FAKE_EXECUTOR, *sys.argv[1:]),
    )
    serve(settings, runner, shutdown_on_signals())
