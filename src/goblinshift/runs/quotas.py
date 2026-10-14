"""Cotas diárias por conta: execuções e bytes de entrada em 24 h corridas (1.7; Frente D).

Calculadas de `run` e `run_input`, sem tabela de contadores (3.3). A checagem roda dentro da
trava global de aceitação, que já serializa as aceitações, e por isso não há write skew.

Contrato fixado na Fase 0 da fatia 6; o corpo é da Frente D.
"""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy.orm import Session


@dataclass(frozen=True)
class DailyUsage:
    runs: int
    input_bytes: int
    # Criação da Run mais antiga ainda na janela: base do `Retry-After` e de `resets_at`.
    oldest_run_at: datetime | None


def daily_usage(session: Session, owner_id: UUID, now: datetime) -> DailyUsage:
    raise NotImplementedError("Frente D da fatia 6")
