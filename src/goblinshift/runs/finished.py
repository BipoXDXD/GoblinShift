"""Evento de término da Run (docs/GoblinShiftPlan.md, 3.7): o span `run.finished`.

Um por Run que chega a estado terminal, emitido depois do commit por quem a concluiu (worker,
claim ou manutenção). É o evento do SLO de execução, `deploy/openobserve/sql/execucao.eventos.sql`;
o contrato dos atributos está no runbook `deploy/openobserve/README.md`.

O span é instantâneo, no momento da emissão: o OpenObserve filtra as janelas dos alertas pelo
início do span, e uma Run de 30 min cairia fora da janela de 15 min se o span começasse com ela.
Sem PII: a conta entra só como `user.hash`; nada de nomes de arquivo nem parâmetros.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from opentelemetry.trace import Tracer
from opentelemetry.util.types import AttributeValue
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.files.candidates import HeldContentMissingError, held_input_contents
from goblinshift.platform.telemetry import UserHasher
from goblinshift.runs.lifecycle import Status
from goblinshift.runs.store import run_transaction
from goblinshift.runs.tables import run, run_input

logger = logging.getLogger(__name__)

SPAN_NAME = "run.finished"
# Run elegível para o SLO de execução (3.7): até 10 arquivos e 50 MiB de entrada.
SLO_MAX_INPUTS = 10
SLO_MAX_INPUT_BYTES = 50 * 1024 * 1024
_MILLISECOND = timedelta(milliseconds=1)


@dataclass(frozen=True, kw_only=True)
class FinishedRun:
    """Os fatos de uma Run terminal que o evento leva.

    `input_bytes` é `None` quando alguma entrada não está mais no storage: sem o tamanho, a Run
    não entra no SLO, em vez de contar com um tamanho inventado.
    """

    run_id: uuid.UUID
    owner_id: uuid.UUID
    recipe_id: str
    recipe_version: int
    status: Status
    error_code: str | None
    attempts: int
    input_count: int
    input_bytes: int | None
    accepted_at: datetime
    started_at: datetime | None
    finished_at: datetime

    @property
    def is_slo_eligible(self) -> bool:
        return (
            self.status is not Status.CANCELED
            and self.input_count <= SLO_MAX_INPUTS
            and self.input_bytes is not None
            and self.input_bytes <= SLO_MAX_INPUT_BYTES
        )

    def attributes(self, hasher: UserHasher) -> dict[str, AttributeValue]:
        attributes: dict[str, AttributeValue] = {
            "main": True,
            "run_id": str(self.run_id),
            "recipe_id": self.recipe_id,
            "recipe_version": self.recipe_version,
            "user.hash": hasher.hash(self.owner_id),
            "run.state": str(self.status),
            "run.attempts": self.attempts,
            "run.input_count": self.input_count,
            "slo.execution.eligible": self.is_slo_eligible,
        }
        if self.error_code is not None:
            attributes["error.code"] = self.error_code
        if self.started_at is not None:
            attributes["run.duration_ms"] = (self.finished_at - self.started_at) // _MILLISECOND
            attributes["run.queue_wait_ms"] = (self.started_at - self.accepted_at) // _MILLISECOND
        return attributes


class RunFinishedReporter:
    """Lê os fatos da Run terminal e emite o `run.finished`."""

    def __init__(self, factory: sessionmaker[Session], hasher: UserHasher, tracer: Tracer) -> None:
        self._factory = factory
        self._hasher = hasher
        self._tracer = tracer

    def report(self, run_id: uuid.UUID) -> None:
        try:
            finished = run_transaction(self._factory, lambda session: _load(session, run_id))
            span = self._tracer.start_span(SPAN_NAME, attributes=finished.attributes(self._hasher))
            span.end()
        except Exception:
            # A Run já está terminal, com os usos liberados; perder o evento custa só telemetria,
            # e propagar faria o worker abandonar a limpeza e a manutenção, o resto do ciclo.
            logger.exception(
                "evento de término da execução perdido", extra={"fields": {"run_id": str(run_id)}}
            )


def _load(session: Session, run_id: uuid.UUID) -> FinishedRun:
    row = session.execute(
        select(
            run.c.owner_id,
            run.c.recipe_id,
            run.c.recipe_version,
            run.c.status,
            run.c.error_code,
            run.c.claim_count,
            run.c.created_at,
            run.c.started_at,
            run.c.finished_at,
        ).where(run.c.id == run_id, run.c.finished_at.is_not(None))
    ).one()
    upload_ids = list(
        session.execute(
            select(run_input.c.upload_id)
            .where(run_input.c.run_id == run_id)
            .order_by(run_input.c.position)
        ).scalars()
    )
    try:
        contents = held_input_contents(session, row.owner_id, upload_ids)
        input_bytes: int | None = sum(content.size_bytes for content in contents)
    except HeldContentMissingError:
        input_bytes = None
    return FinishedRun(
        run_id=run_id,
        owner_id=row.owner_id,
        recipe_id=row.recipe_id,
        recipe_version=row.recipe_version,
        status=Status(row.status),
        error_code=row.error_code,
        attempts=row.claim_count,
        input_count=len(upload_ids),
        input_bytes=input_bytes,
        accepted_at=row.created_at,
        started_at=row.started_at,
        finished_at=row.finished_at,
    )
