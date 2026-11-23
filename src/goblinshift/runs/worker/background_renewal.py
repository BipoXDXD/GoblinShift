"""Renovação do lease em paralelo a uma chamada que bloqueia (issue #442, 14b).

O `start` do executor pode levar até o prazo do executor-ctl, bem mais que o lease. Enquanto a
chamada bloqueia, uma thread renova: a primeira renovação sai já, as seguintes a cada `every`, ou
a cada `retry_every` depois de uma falha transitória do banco (`renew` devolve `None`). Qualquer
outra exceção de `renew` (`LostFencing`, por exemplo) encerra a thread e é relançada na saída do
bloco, se o corpo não tiver levantado a sua.

A thread roda no contexto de quem abriu o bloco (`contextvars`): os logs de renovação levam o
`trace_id` e o `span_id` da tentativa.
"""

import contextvars
import threading
from collections.abc import Callable
from datetime import timedelta
from types import TracebackType
from typing import Self

from goblinshift.runs.lifecycle import Status


def next_renewal_wait(
    renewed: Status | None, *, every: timedelta, retry_every: timedelta
) -> timedelta:
    """Espera até a próxima renovação: curta depois de uma falha transitória (`None`)."""
    return retry_every if renewed is None else every


class BackgroundRenewal:
    def __init__(
        self, renew: Callable[[], Status | None], *, every: timedelta, retry_every: timedelta
    ) -> None:
        self._renew = renew
        self._every = every
        self._retry_every = retry_every
        self._done = threading.Event()
        self._error: Exception | None = None
        self._thread: threading.Thread | None = None

    def __enter__(self) -> Self:
        context = contextvars.copy_context()
        self._thread = threading.Thread(
            target=context.run, args=(self._renew_until_done,), name="lease-renewal", daemon=True
        )
        self._thread.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._done.set()
        if self._thread is not None:
            self._thread.join()
        # A exceção do corpo (ex.: `RunnerError` do start) tem precedência sobre a da thread.
        if exc_type is None and self._error is not None:
            raise self._error

    def _renew_until_done(self) -> None:
        while not self._done.is_set():
            try:
                renewed = self._renew()
            except Exception as error:  # relançada por `__exit__` no chamador
                self._error = error
                return
            wait = next_renewal_wait(renewed, every=self._every, retry_every=self._retry_every)
            self._done.wait(wait.total_seconds())
