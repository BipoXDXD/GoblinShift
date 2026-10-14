"""Purga em lotes do histórico de uma conta com exclusão pedida (docs/GoblinShiftPlan.md, 1.8).

Apaga Runs (com os vínculos internos por CASCADE), `idempotency_record` e `transfer_slot` da
conta. Só roda quando a conta não tem Run não terminal.

Contrato fixado na Fase 0 da fatia 6; o corpo é da Frente C.
"""

from uuid import UUID

from sqlalchemy.orm import Session


def purge_owner_batch(session: Session, owner_id: UUID, limit: int) -> bool:
    """Processa até `limit` Runs; True quando não sobra nada da conta em `runs`."""
    raise NotImplementedError("Frente C da fatia 6")
