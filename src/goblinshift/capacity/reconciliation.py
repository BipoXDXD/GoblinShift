"""Reconciliação dos pools com o livro-razão, para a manutenção (docs/GoblinShiftPlan.md, 3.3).

O livro-razão é a fonte da verdade: cada pool deve valer a soma das reservas vivas do seu escopo.
A reconciliação trava os pools envolvidos na ordem de sempre (contas por `owner_id`, depois o
global), recalcula as somas já com as travas e regrava só o que diverge. Toda reserva e liberação
passa pelo pool global, então, com ele travado, as somas não mudam por baixo.

Também adota detentores sem reserva que quem chama encontrou (objetos criados pela API anterior
durante o deploy da 0006), desde que caibam nos tetos; o que não cabe fica de fora e vira alerta.
"""

import logging
import uuid
from collections.abc import Collection, Sequence
from dataclasses import dataclass

from sqlalchemy import ColumnElement, Select, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from goblinshift.capacity.ledger import (
    HolderKind,
    Resource,
    account_pool,
    ensure_account_pool,
    global_pool,
)
from goblinshift.capacity.tables import capacity_pool, resource_reservation

logger = logging.getLogger(__name__)

_RESOURCE = Resource.STORAGE_BYTES


@dataclass(frozen=True)
class Unreserved:
    """Detentor que ocupa armazenamento sem reserva viva (por exemplo, objeto do deploy)."""

    owner_id: uuid.UUID
    holder: HolderKind
    holder_id: uuid.UUID
    amount: int


@dataclass(frozen=True)
class ReconcileReport:
    # Pools cujo `reserved` divergia da soma das reservas vivas e foi corrigido.
    pools_corrected: int
    # Objetos `committed` sem reserva (criados pela API anterior durante o deploy) adotados.
    objects_adopted: int
    # Detentores sem reserva que não cabiam nos tetos e ficaram de fora (alerta).
    adoptions_refused: int = 0


@dataclass
class _Balance:
    reserved: int
    max_amount: int
    live: int


def reconcile(
    session: Session, *, limit: int, unreserved: Sequence[Unreserved] = ()
) -> ReconcileReport:
    """Recalcula os pools a partir do livro-razão e adota detentores sem reserva, em lote.

    `limit` limita as contas divergentes examinadas e os detentores adotados por chamada; o
    pool global é sempre recalculado.
    """
    candidates = list(unreserved[:limit])
    owners = sorted(_divergent_owners(session, limit) | {c.owner_id for c in candidates})
    for owner_id in owners:
        ensure_account_pool(session, owner_id, _RESOURCE)
    accounts = _lock_accounts(session, owners)
    environment = _lock_global(session)
    corrected = sum(b.reserved != b.live for b in (*accounts.values(), environment))
    adopted, refused = _adopt(session, candidates, accounts, environment)
    for owner_id, balance in accounts.items():
        _store(session, account_pool(owner_id, _RESOURCE), balance)
    _store(session, global_pool(_RESOURCE), environment)
    report = ReconcileReport(corrected, adopted, refused)
    if corrected or refused:
        logger.warning(
            "livro-razão de capacidade divergente",
            extra={
                "fields": {
                    "alert": "capacity_divergence",
                    "pools_corrected": corrected,
                    "objects_adopted": adopted,
                    "adoptions_refused": refused,
                }
            },
        )
    return report


def _divergent_owners(session: Session, limit: int) -> set[uuid.UUID]:
    """Contas cujo pool parece divergir; leitura sem trava, conferida depois sob a trava."""
    live = _live_sums().subquery()
    rows = session.execute(
        select(capacity_pool.c.owner_id)
        .outerjoin(live, live.c.owner_id == capacity_pool.c.owner_id)
        .where(
            capacity_pool.c.scope == "account",
            capacity_pool.c.resource == _RESOURCE,
            capacity_pool.c.reserved != func.coalesce(live.c.total, 0),
        )
        .order_by(capacity_pool.c.owner_id)
        .limit(limit)
    ).scalars()
    return set(rows)


def _lock_accounts(session: Session, owners: Collection[uuid.UUID]) -> dict[uuid.UUID, _Balance]:
    if not owners:
        return {}
    pools = session.execute(
        select(capacity_pool.c.owner_id, capacity_pool.c.reserved, capacity_pool.c.max_amount)
        .where(
            capacity_pool.c.scope == "account",
            capacity_pool.c.resource == _RESOURCE,
            capacity_pool.c.owner_id.in_(owners),
        )
        .order_by(capacity_pool.c.owner_id)
        .with_for_update()
    ).all()
    totals = {
        row.owner_id: row.total
        for row in session.execute(_live_sums().where(resource_reservation.c.owner_id.in_(owners)))
    }
    return {
        pool.owner_id: _Balance(pool.reserved, pool.max_amount, totals.get(pool.owner_id, 0))
        for pool in pools
    }


def _lock_global(session: Session) -> _Balance:
    pool = session.execute(
        select(capacity_pool.c.reserved, capacity_pool.c.max_amount)
        .where(global_pool(_RESOURCE))
        .with_for_update()
    ).one_or_none()
    if pool is None:
        raise RuntimeError("pool global de armazenamento ausente: a migração 0006 não rodou?")
    total: int = session.execute(
        select(func.coalesce(func.sum(resource_reservation.c.amount), 0)).where(
            resource_reservation.c.released_at.is_(None),
            resource_reservation.c.resource == _RESOURCE,
        )
    ).scalar_one()
    return _Balance(pool.reserved, pool.max_amount, total)


def _adopt(
    session: Session,
    candidates: Sequence[Unreserved],
    accounts: dict[uuid.UUID, _Balance],
    environment: _Balance,
) -> tuple[int, int]:
    """Cria a reserva de cada candidato que ainda não tem uma e cabe nos dois tetos."""
    already = _live_holders(session, candidates)
    adopted = refused = 0
    for candidate in candidates:
        if (candidate.holder, candidate.holder_id) in already:
            continue
        account = accounts[candidate.owner_id]
        if not (_fits(account, candidate.amount) and _fits(environment, candidate.amount)):
            refused += 1
            continue
        session.execute(
            insert(resource_reservation).values(
                owner_id=candidate.owner_id,
                holder_kind=candidate.holder,
                holder_id=candidate.holder_id,
                resource=_RESOURCE,
                amount=candidate.amount,
            )
        )
        account.live += candidate.amount
        environment.live += candidate.amount
        already.add((candidate.holder, candidate.holder_id))
        adopted += 1
    return adopted, refused


def _live_holders(
    session: Session, candidates: Sequence[Unreserved]
) -> set[tuple[HolderKind, uuid.UUID]]:
    if not candidates:
        return set()
    rows = session.execute(
        select(resource_reservation.c.holder_kind, resource_reservation.c.holder_id).where(
            resource_reservation.c.released_at.is_(None),
            resource_reservation.c.holder_id.in_({c.holder_id for c in candidates}),
        )
    ).all()
    return {(HolderKind(row.holder_kind), row.holder_id) for row in rows}


def _fits(balance: _Balance, amount: int) -> bool:
    return balance.live + amount <= balance.max_amount


def _store(session: Session, pool: ColumnElement[bool], balance: _Balance) -> None:
    """Regrava o pool com a soma do livro-razão; acima do teto, fica no teto e alerta."""
    target = balance.live
    if target > balance.max_amount:
        logger.error(
            "livro-razão acima do teto do pool",
            extra={"fields": {"alert": "capacity_over_max", "live": target}},
        )
        target = balance.max_amount
    if target != balance.reserved:
        session.execute(update(capacity_pool).where(pool).values(reserved=target))


def _live_sums() -> Select[tuple[uuid.UUID, int]]:
    return (
        select(
            resource_reservation.c.owner_id,
            func.sum(resource_reservation.c.amount).label("total"),
        )
        .where(
            resource_reservation.c.released_at.is_(None),
            resource_reservation.c.resource == _RESOURCE,
        )
        .group_by(resource_reservation.c.owner_id)
    )
