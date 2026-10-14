"""Cotas diárias por conta: execuções e bytes de entrada em 24 h corridas (1.7; Frente D).

Calculadas de `run` e `run_input`, sem tabela de contadores (3.3). A checagem roda dentro da
trava global de aceitação, que já serializa as aceitações, e por isso não há write skew. Só
Runs aceitas contam, inclusive as que depois falham: a recusa não cobra cota.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from goblinshift.files.uploads import stored_size_of
from goblinshift.runs.lifecycle import TERMINAL_STATUSES
from goblinshift.runs.tables import run, run_input

QUOTA_WINDOW = timedelta(hours=24)
MAX_RUNS_PER_WINDOW = 30
MAX_INPUT_BYTES_PER_WINDOW = 2 * 1024**3


@dataclass(frozen=True)
class DailyUsage:
    runs: int
    input_bytes: int
    # Criação da Run mais antiga ainda na janela: base do `Retry-After` e de `resets_at`.
    oldest_run_at: datetime | None

    def resets_at(self) -> datetime | None:
        """Quando a Run mais antiga sai da janela e devolve parte da cota."""
        return None if self.oldest_run_at is None else self.oldest_run_at + QUOTA_WINDOW


def daily_usage(session: Session, owner_id: UUID, now: datetime) -> DailyUsage:
    """Execuções criadas em `(now - 24 h, now]` e a soma dos bytes das entradas delas.

    A mesma entrada usada por duas execuções conta duas vezes: a cota é de entrada processada.
    """
    in_window = (run.c.owner_id == owner_id) & (run.c.created_at > now - QUOTA_WINDOW)
    runs, oldest = session.execute(
        select(func.count(), func.min(run.c.created_at)).where(in_window)
    ).one()
    input_bytes: int = session.execute(
        select(func.coalesce(func.sum(stored_size_of(run_input.c.upload_id)), 0))
        .join(run, run.c.id == run_input.c.run_id)
        .where(in_window, run_input.c.owner_id == owner_id)
    ).scalar_one()
    return DailyUsage(runs=runs, input_bytes=input_bytes, oldest_run_at=oldest)


def active_run_id(session: Session, owner_id: UUID) -> UUID | None:
    """A execução não terminal da conta, se houver (no máximo uma, pelo índice único parcial)."""
    found: UUID | None = session.execute(
        select(run.c.id).where(run.c.owner_id == owner_id, run.c.status.not_in(TERMINAL_STATUSES))
    ).scalar_one_or_none()
    return found
