"""Claim da próxima execução elegível e espera por trabalho (docs/GoblinShiftPlan.md, 3.4).

A própria tabela `run` é a fila. Numa transação curta, o claim trava a primeira linha elegível
com `FOR UPDATE SKIP LOCKED` (dois workers nunca pegam a mesma), incrementa a geração e o
`claim_count`, cria a Attempt com o lease e, na primeira tentativa, fixa `deadline_at` pelo
relógio do banco. O executor só é pedido depois do commit, pelo coordenador.
"""

import logging
import random
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta
from types import TracebackType
from typing import Self

import psycopg
from sqlalchemy import Engine, Select, and_, func, or_, select
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.files.storage import FileStorage
from goblinshift.runs.lifecycle import Claim, Phase, Refused, Status, decide_transition
from goblinshift.runs.store import (
    Applied,
    RunFinishedHook,
    apply_transition,
    complete_after_commit,
    load_run,
    run_transaction,
)
from goblinshift.runs.tables import run, run_input

logger = logging.getLogger(__name__)

# Canal emitido pela aceitação no mesmo commit que grava a Run (3.4).
RUNS_CHANNEL = "goblinshift_runs"
# Execuções venenosas encerradas num mesmo pedido de claim antes de desistir até a próxima vez.
_MAX_POISON_PER_CLAIM = 10
_MILLISECOND = timedelta(milliseconds=1)
# Teto do expoente do backoff: 2**32 s já passa de qualquer teto, e o float não estoura.
_MAX_DOUBLINGS = 32


@dataclass(frozen=True)
class ClaimedAttempt:
    """O que o coordenador precisa da tentativa recém-criada, lido na mesma transação."""

    run_id: uuid.UUID
    owner_id: uuid.UUID
    attempt_id: uuid.UUID
    generation: int
    number: int
    recipe_id: str
    recipe_version: int
    # Imagem do executor fixada na aceitação (`run.executor_digest`), enviada no `start`.
    executor_image: str
    parameters: dict[str, object]
    # Uploads de entrada, na ordem das posições.
    input_upload_ids: tuple[uuid.UUID, ...]
    traceparent: str | None
    # Da elegibilidade da Run (`eligible_at`, que na retentativa já inclui o jitter) ao início da
    # tentativa, os dois pelo relógio do banco na transação do claim (3.7).
    queue_wait_ms: int


def claim_next(
    factory: sessionmaker[Session],
    storage: FileStorage,
    lease: timedelta,
    *,
    on_finished: RunFinishedHook | None = None,
) -> ClaimedAttempt | None:
    """Reivindica a próxima execução elegível, ou `None` se a fila está vazia.

    O claim recusado por excesso (`INFRA_POISON`) encerra a execução e segue para a seguinte.
    """
    for _ in range(_MAX_POISON_PER_CLAIM):
        outcome = run_transaction(factory, lambda session: _claim_one(session, lease))
        if outcome is None or isinstance(outcome, ClaimedAttempt):
            return outcome
        logger.warning(
            "execução encerrada no claim",
            extra={"fields": {"run_id": str(outcome.run_id), "error.code": "INFRA_POISON"}},
        )
        complete_after_commit(factory, storage, outcome, on_finished)
    return None


def _claim_one(session: Session, lease: timedelta) -> ClaimedAttempt | Applied | None:
    run_id = session.execute(_next_eligible()).scalar_one_or_none()
    if run_id is None:
        return None
    loaded = load_run(session, run_id)
    if loaded is None:
        return None
    decision = decide_transition(loaded.state, Claim(loaded.now))
    if isinstance(decision, Refused):
        # A consulta e a máquina usam as mesmas guardas; divergência aqui é defeito, não fila.
        raise RuntimeError(f"claim recusado para {run_id}: {decision.reason}")
    applied = apply_transition(session, loaded, decision, lease=lease)
    if applied.started_attempt_id is None:
        return applied
    details = session.execute(
        select(
            run.c.recipe_id,
            run.c.recipe_version,
            run.c.executor_digest,
            run.c.parameters,
            run.c.traceparent,
        ).where(run.c.id == run_id)
    ).one()
    inputs = session.execute(
        select(run_input.c.upload_id)
        .where(run_input.c.run_id == run_id)
        .order_by(run_input.c.position)
    ).scalars()
    return ClaimedAttempt(
        run_id=run_id,
        owner_id=loaded.owner_id,
        attempt_id=applied.started_attempt_id,
        generation=applied.state.generation,
        number=applied.state.claim_count,
        recipe_id=details.recipe_id,
        recipe_version=details.recipe_version,
        executor_image=details.executor_digest,
        parameters=dict(details.parameters),
        input_upload_ids=tuple(inputs),
        traceparent=details.traceparent,
        queue_wait_ms=max((loaded.now - loaded.state.eligible_at) // _MILLISECOND, 0),
    )


def _next_eligible() -> Select[uuid.UUID]:
    # Mesmas guardas da tabela de 3.5; o prazo vencido fica com a manutenção, senão a linha
    # mais antiga bloquearia a fila inteira.
    now = func.now()
    queued = and_(run.c.status == Status.QUEUED, run.c.queue_expires_at > now)
    retrying = and_(
        run.c.status == Status.RUNNING,
        run.c.phase == Phase.RETRY_WAIT,
        or_(run.c.deadline_at.is_(None), run.c.deadline_at >= now),
    )
    return (
        select(run.c.id)
        .where(run.c.eligible_at <= now, or_(queued, retrying))
        .order_by(run.c.created_at, run.c.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )


def equal_jitter(delay_s: float) -> float:
    """Metade fixa e metade sorteada: espaça os workers sem encurtar a espera abaixo da metade."""
    half = delay_s / 2
    return half + random.uniform(0, half)  # noqa: S311 - jitter, não segredo


@dataclass(frozen=True)
class ReconnectBackoff:
    """Espera entre tentativas de reconectar o listener: dobra a partir de `first` até `cap`."""

    first: timedelta = timedelta(seconds=1)
    cap: timedelta = timedelta(seconds=30)
    # Recebe a espera calculada e devolve a que vale; o padrão nunca passa da calculada.
    jitter: Callable[[float], float] = field(default=equal_jitter)

    def delay_s(self, failures: int) -> float:
        """Espera depois de `failures` tentativas seguidas recusadas (a partir de 1)."""
        if failures < 1:
            raise ValueError(f"falhas seguidas abaixo de 1: {failures}")
        doublings = min(failures - 1, _MAX_DOUBLINGS)
        base = min(self.cap.total_seconds(), self.first.total_seconds() * 2**doublings)
        return self.jitter(base)


class QueueListener:
    """`LISTEN goblinshift_runs` numa conexão própria, em autocommit, fora do pool.

    A queda dessa conexão (PostgreSQL reiniciado com o worker ocioso) não derruba o processo: a
    espera registra o tipo e o SQLSTATE, fecha a conexão e devolve `False`, e o worker segue no
    polling. Cada espera seguinte tenta reconectar quando a anterior foi recusada há mais que o
    backoff; sem conexão, a espera dorme o intervalo do polling. Notificações perdidas nesse meio
    tempo são as que o polling já cobre.
    """

    def __init__(
        self,
        engine: Engine,
        connect_timeout_s: int,
        *,
        backoff: ReconnectBackoff = ReconnectBackoff(),  # noqa: B008 - dataclass imutável
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._conninfo = engine.url.set(drivername="postgresql").render_as_string(
            hide_password=False
        )
        self._connect_timeout_s = connect_timeout_s
        self._backoff = backoff
        self._clock = clock
        self._sleep = sleep
        self._connection: psycopg.Connection[tuple[object, ...]] | None = None
        self._entered = False
        # Tentativas de reconexão recusadas em sequência e quando a próxima pode acontecer.
        self._failures = 0
        self._retry_at = float("-inf")

    def __enter__(self) -> Self:
        # Sem banco na subida, o processo não fica pronto: o erro sobe até `main`.
        self._connection = self._listen()
        self._entered = True
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self._entered = False
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def wait(self, timeout_s: float) -> bool:
        """Espera um NOTIFY até `timeout_s`; `False` se o tempo esgotou (consultar mesmo assim)."""
        if not self._entered:
            raise RuntimeError("QueueListener usado fora do bloco with")
        connection = self._connected()
        if connection is None:
            self._sleep(timeout_s)
            return False
        try:
            return any(True for _ in connection.notifies(timeout=timeout_s, stop_after=1))
        except psycopg.OperationalError as error:
            self._drop(error)
            return False

    def _connected(self) -> psycopg.Connection[tuple[object, ...]] | None:
        """A conexão, reconectando se a espera do backoff já passou; `None` se segue sem."""
        if self._connection is not None:
            return self._connection
        if self._clock() < self._retry_at:
            return None
        try:
            self._connection = self._listen()
        except psycopg.OperationalError as error:
            self._failures += 1
            retry_in_s = self._backoff.delay_s(self._failures)
            self._retry_at = self._clock() + retry_in_s
            fields = {**_error_fields(error), "failures": self._failures, "retry_in_s": retry_in_s}
            logger.warning("listener sem conexão; nova tentativa adiada", extra={"fields": fields})
            return None
        logger.info("listener reconectado", extra={"fields": {"failures": self._failures}})
        self._failures = 0
        return self._connection

    def _listen(self) -> psycopg.Connection[tuple[object, ...]]:
        connection = psycopg.connect(
            self._conninfo, autocommit=True, connect_timeout=self._connect_timeout_s
        )
        try:
            connection.execute(f"LISTEN {RUNS_CHANNEL}")
        except BaseException:
            connection.close()
            raise
        return connection

    def _drop(self, error: psycopg.OperationalError) -> None:
        """Fecha a conexão perdida; a espera seguinte já tenta reconectar."""
        logger.warning(
            "listener perdeu a conexão; seguindo no polling",
            extra={"fields": _error_fields(error)},
        )
        if self._connection is not None:
            self._connection.close()
        self._connection = None


def _error_fields(error: psycopg.Error) -> dict[str, object]:
    # Só o tipo e o SQLSTATE: a mensagem do banco traz host, porta e usuário.
    return {"error_type": type(error).__name__, "sqlstate": error.sqlstate}
