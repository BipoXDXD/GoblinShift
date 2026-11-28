"""Estado grosseiro do serviço para a página `/estado` (D9 do plano de paridade, 3.7).

Só níveis, nunca contagens: a página é pública e não pode dizer quanto o serviço é usado. O dono
de `run` e `attempt` lê as próprias tabelas, numa consulta com o relógio do banco, o mesmo de
todas as decisões de prazo (3.4).

A fila fica cheia no mesmo teto da aceitação (`MAX_QUEUED_RUNS`), ocupada com qualquer execução
esperando e livre sem nenhuma. O processamento fica lento quando a execução elegível há mais tempo
espera mais de `SLOW_AFTER`, e parado quando espera mais de `STALLED_AFTER` sem nenhuma tentativa
com lease vivo: com lease vivo, há worker trabalhando, só que devagar.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session

from goblinshift.runs.acceptance import MAX_QUEUED_RUNS
from goblinshift.runs.lifecycle import Status
from goblinshift.runs.tables import attempt, run

SLOW_AFTER = timedelta(minutes=2)
STALLED_AFTER = timedelta(minutes=10)


class ProcessingLevel(StrEnum):
    NORMAL = "normal"
    SLOW = "slow"
    STALLED = "stalled"


class QueueLevel(StrEnum):
    FREE = "free"
    BUSY = "busy"
    FULL = "full"


@dataclass(frozen=True)
class ServiceStatus:
    processing: ProcessingLevel
    queue: QueueLevel
    checked_at: datetime


@dataclass(frozen=True)
class QueueReading:
    """O que o banco diz da fila; nunca sai deste módulo, só os níveis derivados."""

    queued: int
    # Espera da execução em fila elegível há mais tempo; `None` com a fila vazia. Negativa numa
    # retentativa cujo `eligible_at` (recuo com jitter) ainda não chegou.
    oldest_wait: timedelta | None
    # Alguma tentativa não terminada com o lease ainda dentro do prazo.
    lease_alive: bool


def classify(reading: QueueReading, checked_at: datetime) -> ServiceStatus:
    return ServiceStatus(
        processing=_processing_level(reading),
        queue=_queue_level(reading.queued),
        checked_at=checked_at,
    )


def read_service_status(session: Session) -> ServiceStatus:
    """Uma consulta, na transação de quem chama."""
    now = func.now()
    queued = run.c.status == Status.QUEUED
    row = session.execute(
        select(
            now.label("now"),
            select(func.count()).select_from(run).where(queued).scalar_subquery().label("queued"),
            select(func.min(run.c.eligible_at))
            .where(queued)
            .scalar_subquery()
            .label("oldest_eligible_at"),
            exists()
            .where(attempt.c.ended_at.is_(None), attempt.c.lease_expires_at > now)
            .label("lease_alive"),
        )
    ).one()
    oldest = row.oldest_eligible_at
    reading = QueueReading(
        queued=row.queued,
        oldest_wait=None if oldest is None else row.now - oldest,
        lease_alive=row.lease_alive,
    )
    return classify(reading, row.now)


def _queue_level(queued: int) -> QueueLevel:
    if queued >= MAX_QUEUED_RUNS:
        return QueueLevel.FULL
    if queued > 0:
        return QueueLevel.BUSY
    return QueueLevel.FREE


def _processing_level(reading: QueueReading) -> ProcessingLevel:
    wait = reading.oldest_wait
    if wait is None or wait <= SLOW_AFTER:
        return ProcessingLevel.NORMAL
    if wait > STALLED_AFTER and not reading.lease_alive:
        return ProcessingLevel.STALLED
    return ProcessingLevel.SLOW
