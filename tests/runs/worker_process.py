"""Worker de verdade, num processo próprio, com o executor de teste.

    python tests/runs/worker_process.py \\
        [--pause-while-supervising | --pause-at preparing|collecting|publishing] \\
        <modo do fake_executor…>

Lê as mesmas variáveis GS_* do `gs-worker` e roda o mesmo `serve`; só o comando do executor
muda, para abrir a janela em que o teste mata ou pausa o processo. O log do worker sai também em
`debug`, para o teste saber quando ele ficou ocioso esperando NOTIFY.

Com `--pause-while-supervising`, o worker se pausa (SIGSTOP) na primeira consulta ao estado do
executor vivo, já no acompanhamento. Com `--pause-at preparing`, pausa na criação da área de
trabalho, antes de qualquer cópia de entrada e do início do executor. Com `--pause-at collecting`,
pausa em `collect_outputs`, e com `--pause-at publishing`, no início de `Coordinator.publish`: nos
dois o executor já rodou e a fase (`packaging`, `finalizing`) já está commitada; na publicação os
candidatos já estão gravados. Esses dois pontos não pertencem ao Runner, então a pausa entra por
um patch do módulo do Coordinator, antes de `serve`. Todos os pontos ficam fora de transação: a
pausa nunca segura a trava da Run, ao contrário de um SIGSTOP mandado de fora, que pode cair no
meio da renovação do lease e esconder a Run da manutenção (`SKIP LOCKED`).
"""

import logging
import os
import signal
import sys
import uuid
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import Any
from unittest import mock

import goblinshift.runs.worker.coordinator as coordinator_module
from goblinshift.entrypoints.worker import serve, shutdown_on_signals
from goblinshift.platform.settings import Settings
from goblinshift.platform.telemetry import configure_logging
from goblinshift.runs.worker.collection import collect_outputs
from goblinshift.runs.worker.coordinator import Coordinator
from goblinshift.runs.worker.runner import ExecutorStatus, LocalRunner, Runner

FAKE_EXECUTOR = str(Path(__file__).with_name("fake_executor.py"))
PAUSE_WHILE_SUPERVISING = "--pause-while-supervising"
PAUSE_AT = "--pause-at"
PAUSE_SUPERVISING = "supervising"
PAUSE_PREPARING = "preparing"
PAUSE_COLLECTING = "collecting"
PAUSE_PUBLISHING = "publishing"
PAUSE_AT_POINTS = (PAUSE_PREPARING, PAUSE_COLLECTING, PAUSE_PUBLISHING)
# Mensagem de `serve` antes de cada espera por NOTIFY (nível debug).
IDLE_MESSAGE = "fila vazia; aguardando NOTIFY"


class PauseOnce:
    """Pausa o próprio processo (SIGSTOP) na primeira chamada e nunca mais."""

    def __init__(self) -> None:
        self._paused = False

    def __call__(self) -> None:
        if not self._paused:
            self._paused = True
            os.kill(os.getpid(), signal.SIGSTOP)


class PausingRunner:
    """Runner que pausa o processo uma vez só, no ponto escolhido.

    Em `supervising`, na primeira vez que vê o executor rodando; em `preparing`, logo depois de
    criar a área de trabalho da tentativa.
    """

    def __init__(self, inner: Runner, pause: Callable[[], None], *, at_preparation: bool) -> None:
        self._inner = inner
        self._pause = pause
        self._at_preparation = at_preparation

    def work_dir(self, attempt_id: uuid.UUID) -> Path:
        return self._inner.work_dir(attempt_id)

    def create_work_dir(self, attempt_id: uuid.UUID) -> Path:
        work_dir = self._inner.create_work_dir(attempt_id)
        if self._at_preparation:
            self._pause()
        return work_dir

    def list_work(self) -> list[uuid.UUID]:
        return self._inner.list_work()

    def start(self, attempt_id: uuid.UUID, image: str) -> ExecutorStatus:
        return self._inner.start(attempt_id, image)

    def status(self, attempt_id: uuid.UUID) -> ExecutorStatus:
        status = self._inner.status(attempt_id)
        if status.is_running and not self._at_preparation:
            self._pause()
        return status

    def stop(self, attempt_id: uuid.UUID, grace: timedelta) -> ExecutorStatus:
        return self._inner.stop(attempt_id, grace)

    def remove(self, attempt_id: uuid.UUID) -> None:
        self._inner.remove(attempt_id)


def parse_pause(argv: list[str]) -> tuple[str | None, list[str]]:
    """Separa a opção de pausa (ponto, ou `None`) do modo do executor de teste.

    Só valem as formas `--pause-while-supervising` e `--pause-at <ponto>` (`preparing`,
    `collecting` ou `publishing`), no começo do argv. Qualquer outro argumento `--pause*`
    (`--pause-at=preparing`, as duas opções juntas, ponto desconhecido) falha alto: sem isso
    viraria modo do executor e o teste não pausaria nunca.
    """
    if argv[:1] == [PAUSE_WHILE_SUPERVISING]:
        pause, mode = PAUSE_SUPERVISING, argv[1:]
    elif argv[:1] == [PAUSE_AT] and argv[1:2] and argv[1] in PAUSE_AT_POINTS:
        pause, mode = argv[1], argv[2:]
    else:
        pause, mode = None, argv
    unsupported = [arg for arg in mode if arg.startswith("--pause")]
    if pause is None and mode[:1] and mode[0].startswith("--"):
        unsupported.insert(0, mode[0])
    if unsupported:
        raise SystemExit(
            f"opção de pausa não suportada: {unsupported}; use {PAUSE_WHILE_SUPERVISING}"
            f" ou {PAUSE_AT} {'|'.join(PAUSE_AT_POINTS)}, uma só, antes do modo"
        )
    return pause, mode


def pause_in_coordinator(point: str, pause: Callable[[], None]) -> None:
    """Pausa em `collecting` (antes de `collect_outputs`) ou `publishing` (início de `publish`).

    O patch vale para o processo todo, que só existe para este teste.
    """
    if point == PAUSE_COLLECTING:

        def collect_after_pause(*args: Any, **kwargs: Any) -> Any:
            pause()
            return collect_outputs(*args, **kwargs)

        mock.patch.object(coordinator_module, "collect_outputs", collect_after_pause).start()
    else:
        publish = Coordinator.publish

        def publish_after_pause(self: Coordinator, *args: Any, **kwargs: Any) -> Any:
            pause()
            return publish(self, *args, **kwargs)

        mock.patch.object(Coordinator, "publish", publish_after_pause).start()


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
        pause_once = PauseOnce()
        if pause in (PAUSE_COLLECTING, PAUSE_PUBLISHING):
            pause_in_coordinator(pause, pause_once)
        else:
            runner = PausingRunner(runner, pause_once, at_preparation=pause == PAUSE_PREPARING)
    serve(settings, runner, shutdown_on_signals())


if __name__ == "__main__":
    main(sys.argv[1:])
