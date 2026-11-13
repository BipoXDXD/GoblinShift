"""FIFO no lugar de um objeto do storage, com vigia contra o bloqueio.

Um `open(O_RDONLY)` num FIFO sem escritor bloqueia para sempre. O vigia destrava o leitor
preso depois de `seconds` (abrindo o FIFO com `O_RDWR`), segue destravando os `open` seguintes
e, ao sair, falha o teste: sem ele, um `open` que bloqueia travaria a suíte inteira em vez de
reprovar um teste.
"""

import contextlib
import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

WATCHDOG_SECONDS = 2.0
_RETRY_SECONDS = 0.05


def make_fifo(path: Path) -> None:
    os.mkfifo(path, 0o600)


def _unblock_reader(path: Path) -> None:
    # O_RDWR não bloqueia e conta como escritor: o `open(O_RDONLY)` preso retorna. Se o FIFO já
    # não existe, não há o que destravar.
    with contextlib.suppress(OSError):
        os.close(os.open(path, os.O_RDWR | os.O_NONBLOCK))


def _watch(path: Path, seconds: float, stop: threading.Event, fired: threading.Event) -> None:
    if stop.wait(seconds):
        return
    fired.set()
    while not stop.wait(_RETRY_SECONDS):
        _unblock_reader(path)


class BlockWatchdog:
    """Vigia ligado num ponto qualquer do teste, para cronometrar só o trecho com o FIFO.

    `fail_if_blocked` cobre um bloco inteiro; quem só planta o FIFO no meio de uma operação longa
    (um executor em subprocesso, por exemplo) liga o vigia na hora do plantio, e o custo anterior
    não conta no prazo. Um `open` bloqueado não termina nunca: qualquer prazo o distingue.
    """

    def __init__(self, path: Path, seconds: float = WATCHDOG_SECONDS) -> None:
        self._path = path
        self._seconds = seconds
        self._stop = threading.Event()
        self._fired = threading.Event()
        self._watcher: threading.Thread | None = None

    def start(self) -> None:
        # Um segundo vigia escaparia do `halt` e dividiria o `_fired`: ligar duas vezes é defeito
        # do teste, não algo a tolerar em silêncio.
        if self._watcher is not None:
            raise RuntimeError("o vigia já foi ligado; cada BlockWatchdog liga uma vez só")
        self._watcher = threading.Thread(
            target=_watch, args=(self._path, self._seconds, self._stop, self._fired), daemon=True
        )
        self._watcher.start()

    def halt(self) -> None:
        """Encerra o vigia sem conferir; sem `start`, não há o que encerrar."""
        self._stop.set()
        if self._watcher is not None:
            self._watcher.join()

    def check(self) -> None:
        """Falha o teste se o prazo venceu; chame depois de `halt`."""
        if self._fired.is_set():
            raise AssertionError(
                f"o código passou de {self._seconds}s sem terminar no FIFO do objeto"
            )


@contextmanager
def fail_if_blocked(path: Path, seconds: float = WATCHDOG_SECONDS) -> Iterator[None]:
    watchdog = BlockWatchdog(path, seconds)
    watchdog.start()
    try:
        yield
    finally:
        watchdog.halt()
    # Fora do `finally`: se o bloco falhou, a falha dele é a que importa.
    watchdog.check()
