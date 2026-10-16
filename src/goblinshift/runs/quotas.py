"""Cotas diárias por conta: execuções e bytes de entrada em 24 h corridas (1.7; Frente D).

Calculadas de `run` e `run_input`, sem tabela de contadores (3.3). A checagem roda dentro da
trava global de aceitação, que já serializa as aceitações, e por isso não há write skew. Só
Runs aceitas contam, inclusive as que depois falham: a recusa não cobra cota.

Os tetos vêm das settings (`GS_QUOTA_*`), com override por conta em `account_quota`, gravado
pelo `gs-admin set-limits`. O tempo de executor (1.8) é consumo informado, sem teto: sai das
tentativas da janela.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Self
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from goblinshift.files.uploads import stored_size_of
from goblinshift.platform.settings import Settings
from goblinshift.runs.lifecycle import TERMINAL_STATUSES
from goblinshift.runs.tables import account_quota, attempt, run, run_input

QUOTA_WINDOW = timedelta(hours=24)
MAX_RUNS_PER_WINDOW = 30
MAX_INPUT_BYTES_PER_WINDOW = 2 * 1024**3
_MS_PER_S = 1000


@dataclass(frozen=True)
class QuotaLimits:
    """Os tetos diários que valem para uma conta."""

    runs_per_24h: int
    input_bytes_per_24h: int

    @classmethod
    def from_settings(cls, settings: Settings) -> Self:
        return cls(settings.quota_runs_per_24h, settings.quota_input_bytes_per_24h)


# Os limites iniciais de 1.7, iguais aos defaults das settings.
DEFAULT_QUOTA = QuotaLimits(MAX_RUNS_PER_WINDOW, MAX_INPUT_BYTES_PER_WINDOW)


@dataclass(frozen=True)
class QuotaOverride:
    """O que o administrador fixou para a conta; `None` é "vale o padrão"."""

    runs_per_24h: int | None = None
    input_bytes_per_24h: int | None = None

    def over(self, defaults: QuotaLimits) -> QuotaLimits:
        return QuotaLimits(
            runs_per_24h=self.runs_per_24h or defaults.runs_per_24h,
            input_bytes_per_24h=self.input_bytes_per_24h or defaults.input_bytes_per_24h,
        )


def quota_override(session: Session, owner_id: UUID) -> QuotaOverride:
    row = session.execute(
        select(account_quota.c.runs_per_24h, account_quota.c.input_bytes_per_24h).where(
            account_quota.c.owner_id == owner_id
        )
    ).one_or_none()
    if row is None:
        return QuotaOverride()
    return QuotaOverride(row.runs_per_24h, row.input_bytes_per_24h)


def quota_for(session: Session, owner_id: UUID, defaults: QuotaLimits) -> QuotaLimits:
    """Os tetos da conta: o override do administrador onde houver, o padrão no resto."""
    return quota_override(session, owner_id).over(defaults)


def set_account_quota(
    session: Session,
    owner_id: UUID,
    *,
    runs_per_24h: int | None,
    input_bytes_per_24h: int | None,
) -> None:
    """Grava o override da conta; `None` volta o item ao padrão. Repetir é seguro.

    Valor zero ou negativo viola o CHECK do banco (`IntegrityError`): quem chama valida antes.
    """
    values = {"runs_per_24h": runs_per_24h, "input_bytes_per_24h": input_bytes_per_24h}
    statement = insert(account_quota).values(owner_id=owner_id, **values)
    session.execute(
        statement.on_conflict_do_update(
            index_elements=[account_quota.c.owner_id], set_={**values, "updated_at": func.now()}
        )
    )


def executor_time_ms(session: Session, owner_id: UUID, now: datetime) -> int:
    """Milissegundos de executor das tentativas que começaram a trabalhar em `(now - 24 h, now]`.

    Tentativa viva conta até `now`; o claim que não chegou ao executor não conta (3.4).
    """
    ended = func.least(func.coalesce(attempt.c.ended_at, now), now)
    seconds = func.extract("epoch", ended - attempt.c.executor_started_at)
    total = session.execute(
        select(func.coalesce(func.sum(seconds), 0))
        .join(run, run.c.id == attempt.c.run_id)
        .where(
            run.c.owner_id == owner_id,
            attempt.c.executor_started_at > now - QUOTA_WINDOW,
            attempt.c.executor_started_at <= now,
        )
    ).scalar_one()
    return round(total * _MS_PER_S)


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
