"""Worker de verdade, num processo próprio, com o executor de teste.

    python tests/runs/worker_process.py [--pause-while-supervising | --pause-at preparing] \\
        <modo do fake_executor…>

Lê as mesmas variáveis GS_* do `gs-worker` e roda o mesmo `serve`; só o comando do executor
muda, para abrir a janela em que o teste mata ou pausa o processo. O log do worker sai também em
`debug`, para o teste saber quando ele ficou ocioso esperando NOTIFY.

Com `--pause-while-supervising`, o worker se pausa (SIGSTOP) na primeira consulta ao estado do
executor vivo, já no acompanhamento. Com `--pause-at preparing`, pausa na criação da área de
trabalho, antes de qualquer cópia de entrada e do início do executor. Os dois pontos ficam fora de
transação: a pausa nunca segura a trava da Run, ao contrário de um SIGSTOP mandado de fora, que
pode cair no meio da renovação do lease e esconder a Run da manutenção (`SKIP LOCKED`).
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
PAUSE_AT = "--pause-at"
PAUSE_SUPERVISING = "supervising"
PAUSE_PREPARING = "preparing"
# Mensagem de `serve` antes de cada espera por NOTIFY (nível debug).
IDLE_MESSAGE = "fila vazia; aguardando NOTIFY"


class PausingRunner:
    """Runner que pausa o próprio processo uma vez só, no ponto escolhido.

    Em `supervising`, na primeira vez que vê o executor rodando; em `preparing`, logo depois de
    criar a área de trabalho da tentativa.
    """

    def __init__(self, inner: Runner, *, at_preparation: bool = False) -> None:
        self._inner = inner
        self._at_preparation = at_preparation
        self._paused = False

    def _pause_once(self) -> None:
        if not self._paused:
            self._paused = True
            os.kill(os.getpid(), signal.SIGSTOP)

    def work_dir(self, attempt_id: uuid.UUID) -> Path:
        return self._inner.work_dir(attempt_id)

    def create_work_dir(self, attempt_id: uuid.UUID) -> Path:
        work_dir = self._inner.create_work_dir(attempt_id)
        if self._at_preparation:
            self._pause_once()
        return work_dir

    def list_work(self) -> list[uuid.UUID]:
        return self._inner.list_work()

    def start(self, attempt_id: uuid.UUID, image: str) -> ExecutorStatus:
        return self._inner.start(attempt_id, image)

    def status(self, attempt_id: uuid.UUID) -> ExecutorStatus:
        status = self._inner.status(attempt_id)
        if status.is_running and not self._at_preparation:
            self._pause_once()
        return status

    def stop(self, attempt_id: uuid.UUID, grace: timedelta) -> ExecutorStatus:
        return self._inner.stop(attempt_id, grace)

    def remove(self, attempt_id: uuid.UUID) -> None:
        self._inner.remove(attempt_id)


def parse_pause(argv: list[str]) -> tuple[str | None, list[str]]:
    """Separa a opção de pausa (ponto, ou `None`) do modo do executor de teste.

    Só valem as formas `--pause-while-supervising` e `--pause-at preparing`, no começo do argv.
    Qualquer outro argumento `--pause*` (`--pause-at=preparing`, as duas opções juntas, ponto
    desconhecido) falha alto: sem isso viraria modo do executor e o teste não pausaria nunca.
    """
    if argv[:1] == [PAUSE_WHILE_SUPERVISING]:
        pause, mode = PAUSE_SUPERVISING, argv[1:]
    elif argv[:2] == [PAUSE_AT, PAUSE_PREPARING]:
        pause, mode = PAUSE_PREPARING, argv[2:]
    else:
        pause, mode = None, argv
    unsupported = [arg for arg in mode if arg.startswith("--pause")]
    if pause is None and mode[:1] and mode[0].startswith("--"):
        unsupported.insert(0, mode[0])
    if unsupported:
        raise SystemExit(
            f"opção de pausa não suportada: {unsupported}; use {PAUSE_WHILE_SUPERVISING}"
            f" ou {PAUSE_AT} {PAUSE_PREPARING}, uma só, antes do modo"
        )
    return pause, mode


def main(argv: list[str]) -> None:
    configure_logging()
    logging.getLogger("goblinshift.worker").setLevel(logging.DEBUG)
    pause, mode = parse_pause(argv)
    settings = Settings()
    runner: Runner = LocalRunner(
        settings.work_root,
        attempt_timeout=timedelta(seconds=settings.attempt_timeout_s),
        command=(sys.executable, FAKE_EXECUTOR, *mode),
    )
    if pause is not None:
        runner = PausingRunner(runner, at_preparation=pause == PAUSE_PREPARING)
    serve(settings, runner, shutdown_on_signals())


if __name__ == "__main__":
    main(sys.argv[1:])
