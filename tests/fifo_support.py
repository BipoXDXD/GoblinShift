"""FIFO no lugar de um objeto do storage, com vigia contra o bloqueio.

Um `open(O_RDONLY)` num FIFO sem escritor bloqueia para sempre. O vigia destrava o leitor
preso depois de `seconds` (abrindo o FIFO com `O_RDWR`), segue destravando os `open` seguintes
e, ao sair, falha o teste: sem ele, um `open` que bloqueia travaria a suíte inteira em vez de
reprovar um teste.
"""

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
    try:
        # O_RDWR não bloqueia e conta como escritor: o `open(O_RDONLY)` preso retorna.
        os.close(os.open(path, os.O_RDWR | os.O_NONBLOCK))
    except OSError:
        pass  # O FIFO já não existe: nada a destravar.


def _watch(path: Path, seconds: float, stop: threading.Event, fired: threading.Event) -> None:
    if stop.wait(seconds):
        return
    fired.set()
    while not stop.wait(_RETRY_SECONDS):
        _unblock_reader(path)


@contextmanager
def fail_if_blocked(path: Path, seconds: float = WATCHDOG_SECONDS) -> Iterator[None]:
    stop = threading.Event()
    fired = threading.Event()
    watcher = threading.Thread(target=_watch, args=(path, seconds, stop, fired), daemon=True)
    watcher.start()
    try:
        yield
    finally:
        stop.set()
        watcher.join()
    if fired.is_set():
        raise AssertionError(f"o código passou de {seconds}s sem terminar no FIFO do objeto")
