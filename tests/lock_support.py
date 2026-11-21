"""Concorrência com espera comprovada pela trava (#442).

A sessão 1 abre a transação e executa a operação que trava; a concorrente roda numa thread; o
teste confirma no `pg_stat_activity` que ela está esperando uma trava e só então faz o commit da
sessão 1. Com uma barreira antes de abrir as transações, as duas podem rodar em série, e o teste
passa mesmo sem a trava.
"""

import threading
import time
from collections.abc import Callable

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

# Abaixo do `lock_timeout` de 2 s do `make_engine`: a concorrente ainda está esperando.
LOCK_WAIT_DEADLINE_S = 1.5
JOIN_TIMEOUT_S = 5.0


def wait_until_someone_waits_for_a_lock(
    factory: sessionmaker[Session], who: str = "a operação concorrente"
) -> None:
    deadline = time.monotonic() + LOCK_WAIT_DEADLINE_S
    with factory() as session:
        while time.monotonic() < deadline:
            waiting = session.execute(
                text(
                    "SELECT count(*) FROM pg_stat_activity"
                    " WHERE datname = current_database() AND wait_event_type = 'Lock'"
                )
            ).scalar_one()
            session.rollback()
            if waiting:
                return
            threading.Event().wait(0.01)
    raise AssertionError(f"{who} não chegou a esperar a trava")


class InThread[T]:
    """Roda `work` numa thread; `result()` devolve o retorno ou relança a exceção dela."""

    def __init__(self, work: Callable[[], T]) -> None:
        self._work = work
        self._returned: list[T] = []
        self._raised: list[BaseException] = []
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        try:
            self._returned.append(self._work())
        except BaseException as error:  # relançada no teste por `result()`
            self._raised.append(error)

    def result(self) -> T:
        self._thread.join(JOIN_TIMEOUT_S)
        assert not self._thread.is_alive(), "a thread não terminou: possível deadlock"
        if self._raised:
            raise self._raised[0]
        return self._returned[0]


def in_thread[T](work: Callable[[], T]) -> InThread[T]:
    return InThread(work)
