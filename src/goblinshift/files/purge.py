"""Purga em lotes dos arquivos de uma conta com exclusão pedida (docs/GoblinShiftPlan.md, 1.8).

Pede a exclusão dos objetos e, quando nenhum sobrar, apaga as linhas de `upload`, `artifact` e
`stored_object`. A remoção física segue o caminho normal, que espera os usos abertos (downloads
de até 10 min) e libera as reservas de capacidade.

Contrato fixado na Fase 0 da fatia 6; o corpo é da Frente C.
"""

from uuid import UUID

from sqlalchemy.orm import Session


def purge_owner_batch(session: Session, owner_id: UUID, limit: int) -> bool:
    """Processa até `limit` itens; True quando não sobra nada da conta em `files`."""
    raise NotImplementedError("Frente C da fatia 6")
