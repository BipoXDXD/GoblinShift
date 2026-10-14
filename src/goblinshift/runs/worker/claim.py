"""Claim da próxima execução elegível e espera por trabalho (docs/GoblinShiftPlan.md, 3.4).

A própria tabela `run` é a fila. Numa transação curta, o claim trava a primeira linha elegível
com `FOR UPDATE SKIP LOCKED` (dois workers nunca pegam a mesma), incrementa a geração e o
`claim_count`, cria a Attempt com o lease e, na primeira tentativa, fixa `deadline_at` pelo
relógio do banco. O executor só é pedido depois do commit, pelo coordenador.
"""

import logging
import uuid
from dataclasses import dataclass
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
    parameters: dict[str, object]
    # Uploads de entrada, na ordem das posições.
    input_upload_ids: tuple[uuid.UUID, ...]
    traceparent: str | None


def claim_next(
    factory: sessionmaker[Session], storage: FileStorage, lease: timedelta
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
        complete_after_commit(factory, storage, outcome)
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
        select(run.c.recipe_id, run.c.recipe_version, run.c.parameters, run.c.traceparent).where(
            run.c.id == run_id
        )
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
        parameters=dict(details.parameters),
        input_upload_ids=tuple(inputs),
        traceparent=details.traceparent,
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


class QueueListener:
    """`LISTEN goblinshift_runs` numa conexão própria, em autocommit, fora do pool."""

    def __init__(self, engine: Engine, connect_timeout_s: int) -> None:
        self._conninfo = engine.url.set(drivername="postgresql").render_as_string(
            hide_password=False
        )
        self._connect_timeout_s = connect_timeout_s
        self._connection: psycopg.Connection[tuple[object, ...]] | None = None

    def __enter__(self) -> Self:
        self._connection = psycopg.connect(
            self._conninfo, autocommit=True, connect_timeout=self._connect_timeout_s
        )
        self._connection.execute(f"LISTEN {RUNS_CHANNEL}")
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._connection is not None:
            self._connection.close()

    def wait(self, timeout_s: float) -> bool:
        """Espera um NOTIFY até `timeout_s`; `False` se o tempo esgotou (consultar mesmo assim)."""
        if self._connection is None:
            raise RuntimeError("QueueListener usado fora do bloco with")
        return any(True for _ in self._connection.notifies(timeout=timeout_s, stop_after=1))
