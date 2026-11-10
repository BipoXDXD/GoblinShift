"""Parada dos processos de fundo (`gs-worker`, `gs-maintenance`) pedida por SIGTERM ou SIGINT.

O handler de sinal só marca uma flag, sem trava nem I/O, e quem a vê é o laço do processo, que
registra o pedido ao sair. O CPython roda o handler na thread principal entre dois bytecodes:

- `threading.Event.set()` toma a trava interna do Event; se o sinal chega enquanto a própria
  `Event.wait` do laço a segura, o handler espera por ela para sempre;
- um log escreve no stream do processo; se o sinal interrompe uma escrita nesse stream, a nova
  escrita levanta `RuntimeError: reentrant call` no ponto interrompido.
"""

import signal
import time
from collections.abc import Callable
from types import FrameType

# Fatia da espera: o maior atraso entre o sinal e a saída quando o processo está ocioso.
SHUTDOWN_POLL_S = 0.5


class ShutdownFlag:
    """Pedido de parada que um handler de sinal pode marcar com segurança.

    `request` é uma atribuição, sem trava, e `wait` dorme em fatias de `slice_s`, conferindo a
    flag entre elas.
    """

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        slice_s: float = SHUTDOWN_POLL_S,
    ) -> None:
        self._clock = clock
        self._sleep = sleep
        self._slice_s = slice_s
        self._signal: int | None = None
        self._requested = False

    @property
    def signum(self) -> int | None:
        """O sinal que pediu a parada; `None` sem sinal."""
        return self._signal

    def is_set(self) -> bool:
        return self._requested

    def request(self, signum: int | None = None) -> None:
        self._signal = signum
        self._requested = True

    def wait(self, timeout_s: float) -> bool:
        """Dorme até `timeout_s` ou até a parada ser pedida; devolve se ela foi pedida."""
        deadline = self._clock() + timeout_s
        while not self._requested:
            remaining = deadline - self._clock()
            if remaining <= 0:
                break
            self._sleep(min(remaining, self._slice_s))
        return self._requested


def shutdown_on_signals() -> ShutdownFlag:
    """Flag de parada marcada por SIGTERM e SIGINT; quem a vê é o laço, que registra o pedido."""
    shutdown = ShutdownFlag()

    def request_shutdown(signum: int, _frame: FrameType | None) -> None:
        shutdown.request(signum)

    signal.signal(signal.SIGTERM, request_shutdown)
    signal.signal(signal.SIGINT, request_shutdown)
    return shutdown
