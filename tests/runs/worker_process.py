"""Worker de verdade, num processo próprio, com o executor de teste.

    python tests/runs/worker_process.py [--pause-while-supervising] <modo do fake_executor…>

Lê as mesmas variáveis GS_* do `gs-worker` e roda o mesmo `serve`; só o comando do executor
muda, para abrir a janela em que o teste mata ou pausa o processo. O log do worker sai também em
`debug`, para o teste saber quando ele ficou ocioso esperando NOTIFY.

Com `--pause-while-supervising`, o worker se pausa (SIGSTOP) na primeira consulta ao estado do
executor vivo, já no acompanhamento. Essa consulta acontece fora de transação: a pausa nunca
segura a trava da Run, ao contrário de um SIGSTOP mandado de fora, que pode cair no meio da
renovação do lease e esconder a Run da manutenção (`SKIP LOCKED`).
"""

import logging
import os
import signal
import sys
import uuid
from datetime import timedelta
from pathlib import Path

from goblinshift.entrypoints.worker import serve, shutdown_on_signals
from goblinshift.platform.settings import Settings
from goblinshift.platform.telemetry import configure_logging
from goblinshift.runs.worker.runner import ExecutorStatus, LocalRunner, Runner

FAKE_EXECUTOR = str(Path(__file__).with_name("fake_executor.py"))
PAUSE_WHILE_SUPERVISING = "--pause-while-supervising"
# Mensagem de `serve` antes de cada espera por NOTIFY (nível debug).
IDLE_MESSAGE = "fila vazia; aguardando NOTIFY"


class PausingRunner:
    """Runner que pausa o próprio processo na primeira vez que vê o executor rodando."""

    def __init__(self, inner: Runner) -> None:
        self._inner = inner
        self._paused = False

    def work_dir(self, attempt_id: uuid.UUID) -> Path:
        return self._inner.work_dir(attempt_id)

    def create_work_dir(self, attempt_id: uuid.UUID) -> Path:
        return self._inner.create_work_dir(attempt_id)

    def list_work(self) -> list[uuid.UUID]:
        return self._inner.list_work()

    def start(self, attempt_id: uuid.UUID, image: str) -> ExecutorStatus:
        return self._inner.start(attempt_id, image)

    def status(self, attempt_id: uuid.UUID) -> ExecutorStatus:
        status = self._inner.status(attempt_id)
        if status.is_running and not self._paused:
            self._paused = True
            os.kill(os.getpid(), signal.SIGSTOP)
        return status

    def stop(self, attempt_id: uuid.UUID, grace: timedelta) -> ExecutorStatus:
        return self._inner.stop(attempt_id, grace)

    def remove(self, attempt_id: uuid.UUID) -> None:
        self._inner.remove(attempt_id)


def main(argv: list[str]) -> None:
    configure_logging()
    logging.getLogger("goblinshift.worker").setLevel(logging.DEBUG)
    pause = argv[:1] == [PAUSE_WHILE_SUPERVISING]
    mode = argv[1:] if pause else argv
    settings = Settings()
    runner: Runner = LocalRunner(
        settings.work_root,
        attempt_timeout=timedelta(seconds=settings.attempt_timeout_s),
        command=(sys.executable, FAKE_EXECUTOR, *mode),
    )
    if pause:
        runner = PausingRunner(runner)
    serve(settings, runner, shutdown_on_signals())


if __name__ == "__main__":
    main(sys.argv[1:])
