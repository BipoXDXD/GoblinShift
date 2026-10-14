"""Última etapa da purga de uma conta com exclusão pedida (docs/GoblinShiftPlan.md, 1.8; Frente C).

Roda depois que `files` e `runs` apagaram tudo o que é da conta: remove chaves, sessões, o pool
de capacidade (`capacity.purge_owner`) e a própria `app_user`. A auditoria fica (ADR 025).

Contrato fixado na Fase 0 da fatia 6; o corpo é da Frente C.
"""

from uuid import UUID

from sqlalchemy.orm import Session


def purge_owner(session: Session, owner_id: UUID) -> None:
    raise NotImplementedError("Frente C da fatia 6")
