"""Vagas de transferência simultânea (docs/GoblinShiftPlan.md, 1.7).

Envio e download de conteúdo não contam na cota por minuto: são limitados por vagas, 2 por conta
e 4 no ambiente em cada direção. As vagas ficam numa tabela, para valer entre réplicas da API.

"Contar e depois inserir" seria write skew sob READ COMMITTED: dois pedidos veriam a mesma
contagem e passariam juntos. Por isso a reserva serializa por direção com um advisory lock de
transação; a seção crítica é uma contagem sobre poucas linhas, então a fila é curta.

Uma vaga tem prazo (`TRANSFER_SLOT_LEASE`), maior que a transferência mais longa permitida. Se a
réplica cair sem liberá-la, ela deixa de contar sozinha e a próxima reserva a remove.

As funções rodam na transação de quem chama; a reserva deve ser confirmada antes da
transferência começar, e a liberação, numa transação própria depois dela.
"""

import uuid

from sqlalchemy import delete, func, insert, select
from sqlalchemy.orm import Session

from goblinshift.files.errors import TransferLimitReachedError
from goblinshift.files.limits import (
    MAX_TRANSFERS_PER_ACCOUNT,
    MAX_TRANSFERS_PER_ENVIRONMENT,
    TRANSFER_SLOT_LEASE,
)
from goblinshift.files.states import TransferDirection
from goblinshift.files.tables import transfer_slot

_LOCK_NAMESPACE = "goblinshift.files.transfer_slot"


def acquire_transfer_slot(
    session: Session, owner_id: uuid.UUID, direction: TransferDirection
) -> uuid.UUID:
    """Reserva uma vaga e devolve o seu ID; sem vaga → `TransferLimitReachedError`."""
    session.execute(
        select(func.pg_advisory_xact_lock(func.hashtext(f"{_LOCK_NAMESPACE}.{direction}")))
    )
    session.execute(
        delete(transfer_slot).where(
            transfer_slot.c.direction == direction, transfer_slot.c.expires_at <= func.now()
        )
    )
    total, mine = session.execute(
        select(
            func.count(),
            func.count().filter(transfer_slot.c.owner_id == owner_id),
        ).where(transfer_slot.c.direction == direction)
    ).one()
    if total >= MAX_TRANSFERS_PER_ENVIRONMENT or mine >= MAX_TRANSFERS_PER_ACCOUNT:
        raise TransferLimitReachedError
    slot_id: uuid.UUID = session.execute(
        insert(transfer_slot)
        .values(
            owner_id=owner_id,
            direction=direction,
            expires_at=func.now() + TRANSFER_SLOT_LEASE,
        )
        .returning(transfer_slot.c.id)
    ).scalar_one()
    return slot_id


def release_transfer_slot(session: Session, slot_id: uuid.UUID) -> None:
    """Devolve a vaga; liberar o que já foi liberado (ou venceu) é sucesso."""
    session.execute(delete(transfer_slot).where(transfer_slot.c.id == slot_id))
