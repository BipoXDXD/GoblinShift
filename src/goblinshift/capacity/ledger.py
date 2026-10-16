"""Reservas, liberações e transferências no livro-razão (docs/GoblinShiftPlan.md, 1.7 e 3.3).

O saldo nunca é conferido antes de escrito: cada reserva é um `UPDATE` condicional no pool
(`reserved + :n <= max_amount`), e o `CHECK (reserved BETWEEN 0 AND max_amount)` do banco
segura o resto. Ordem das travas, sempre: pools de conta (por `owner_id`) e depois o global. As
funções rodam na transação de quem chama; uma exceção de capacidade exige desfazê-la, porque o
pool da conta pode já ter sido incrementado quando o global recusa.
"""

import uuid
from collections import defaultdict
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import NoReturn

from sqlalchemy import ColumnElement, and_, case, delete, exists, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from goblinshift.capacity.tables import capacity_pool, resource_reservation

GIB = 1024**3
# Tetos de 1.7; gravados no pool quando ele nasce (o global pela migração 0006).
GLOBAL_STORAGE_MAX_BYTES = 20 * GIB
ACCOUNT_STORAGE_MAX_BYTES = 4 * GIB

# Reservas liberadas ficam como trilha do livro-razão pelo mesmo prazo do histórico (1.7).
RELEASED_RETENTION = timedelta(days=90)

_GLOBAL = "global"
_ACCOUNT = "account"


class Resource(StrEnum):
    STORAGE_BYTES = "storage_bytes"


class HolderKind(StrEnum):
    UPLOAD = "upload"
    RUN = "run"
    OBJECT = "object"


# Teto da conta por recurso, gravado no pool da conta quando ele nasce.
ACCOUNT_MAX: dict[Resource, int] = {Resource.STORAGE_BYTES: ACCOUNT_STORAGE_MAX_BYTES}


class AccountCapacityExceededError(Exception):
    """Reserva acima do teto da conta: 429 `QUOTA_EXCEEDED`, com `Retry-After`."""


class GlobalCapacityExceededError(Exception):
    """Reserva acima do teto do ambiente: 503 `SERVICE_UNAVAILABLE`, com `Retry-After`."""


class StorageBelowReservedError(ValueError):
    """O teto pedido para a conta é menor do que ela já reservou."""


class ReservationNotFoundError(LookupError):
    """O detentor de origem de uma transferência não tem reserva viva."""


class InsufficientReservationError(ValueError):
    """A transferência pede mais do que a reserva de origem tem."""


@dataclass(frozen=True)
class CapacityUsage:
    storage_reserved_bytes: int
    storage_max_bytes: int


def reserve(
    session: Session,
    *,
    owner_id: uuid.UUID,
    holder: HolderKind,
    holder_id: uuid.UUID,
    resource: Resource,
    amount: int,
) -> None:
    """Reserva `amount` na conta e no ambiente, tudo ou nada; acima do teto levanta.

    Um detentor tem no máximo uma reserva viva por recurso: repetir viola o índice único
    parcial (`IntegrityError`), porque é erro de quem chama, não estado a tolerar.
    """
    reserve_many(
        session,
        owner_id=owner_id,
        holder=holder,
        resource=resource,
        amounts={holder_id: amount},
    )


def reserve_many(
    session: Session,
    *,
    owner_id: uuid.UUID,
    holder: HolderKind,
    resource: Resource,
    amounts: Mapping[uuid.UUID, int],
) -> None:
    """Reserva para vários detentores da mesma conta com um único passo nos pools.

    O lote inteiro cabe ou nada é reservado: a soma vai num só `UPDATE` condicional por pool.
    """
    for amount in amounts.values():
        _require_non_negative(amount)
    if not amounts:
        return
    total = sum(amounts.values())
    ensure_account_pool(session, owner_id, resource)
    if not _take(session, account_pool(owner_id, resource), total):
        raise AccountCapacityExceededError(f"conta {owner_id}: {total} de {resource}")
    if not _take(session, global_pool(resource), total):
        _require_global_pool(session, resource)
        raise GlobalCapacityExceededError(f"ambiente: {total} de {resource}")
    session.execute(
        insert(resource_reservation),
        [
            {
                "owner_id": owner_id,
                "holder_kind": holder,
                "holder_id": holder_id,
                "resource": resource,
                "amount": amount,
            }
            for holder_id, amount in amounts.items()
        ],
    )


def release(session: Session, *, holder: HolderKind, holder_id: uuid.UUID) -> int:
    """Libera a reserva viva do detentor e devolve a quantidade; repetir devolve 0."""
    return release_many(session, holder=holder, holder_ids=[holder_id])


def release_many(session: Session, *, holder: HolderKind, holder_ids: Collection[uuid.UUID]) -> int:
    """Libera as reservas vivas dos detentores e devolve o total; os já liberados contam 0.

    Trava as reservas em ordem de ID e devolve aos pools das contas, em ordem de conta, antes do
    global: um lote da manutenção com várias contas segue a mesma ordem de uma reserva.
    """
    ids = sorted(set(holder_ids))
    if not ids:
        return 0
    live = and_(
        resource_reservation.c.holder_kind == holder,
        resource_reservation.c.holder_id.in_(ids),
        resource_reservation.c.released_at.is_(None),
    )
    locked = (
        select(resource_reservation.c.id)
        .where(live)
        .order_by(resource_reservation.c.id)
        .with_for_update()
    )
    released = session.execute(
        update(resource_reservation)
        .where(resource_reservation.c.id.in_(locked.scalar_subquery()))
        .values(released_at=func.now())
        .returning(
            resource_reservation.c.owner_id,
            resource_reservation.c.resource,
            resource_reservation.c.amount,
        )
    ).all()
    per_account: dict[tuple[uuid.UUID, Resource], int] = defaultdict(int)
    per_resource: dict[Resource, int] = defaultdict(int)
    for row in released:
        per_account[row.owner_id, Resource(row.resource)] += row.amount
        per_resource[Resource(row.resource)] += row.amount
    for (owner_id, resource), amount in sorted(per_account.items()):
        _give_back(session, account_pool(owner_id, resource), amount)
    for resource, amount in sorted(per_resource.items()):
        _give_back(session, global_pool(resource), amount)
    return sum(per_resource.values())


def transfer(
    session: Session,
    *,
    from_holder: tuple[HolderKind, uuid.UUID],
    to_holder: tuple[HolderKind, uuid.UUID],
    amount: int,
) -> None:
    """Passa `amount` da reserva de um detentor para outro, sem tocar os pools.

    A origem que chega a zero é encerrada. O destino, se já tiver reserva viva, soma; ele precisa
    ser da mesma conta. Sem reserva de origem → `ReservationNotFoundError`; com menos do que o
    pedido → `InsufficientReservationError`.
    """
    _require_non_negative(amount)
    from_kind, from_id = from_holder
    to_kind, to_id = to_holder
    source = session.execute(
        update(resource_reservation)
        .where(
            resource_reservation.c.holder_kind == from_kind,
            resource_reservation.c.holder_id == from_id,
            resource_reservation.c.resource == Resource.STORAGE_BYTES,
            resource_reservation.c.released_at.is_(None),
            resource_reservation.c.amount >= amount,
        )
        .values(
            amount=resource_reservation.c.amount - amount,
            # Só a origem esvaziada é encerrada; com saldo, continua viva.
            released_at=case((resource_reservation.c.amount == amount, func.now()), else_=None),
        )
        .returning(resource_reservation.c.owner_id)
    ).scalar_one_or_none()
    if source is None:
        _raise_transfer_refusal(session, from_kind, from_id, amount)
    statement = insert(resource_reservation).values(
        owner_id=source,
        holder_kind=to_kind,
        holder_id=to_id,
        resource=Resource.STORAGE_BYTES,
        amount=amount,
    )
    received = session.execute(
        statement.on_conflict_do_update(
            index_elements=[
                resource_reservation.c.holder_kind,
                resource_reservation.c.holder_id,
                resource_reservation.c.resource,
            ],
            index_where=resource_reservation.c.released_at.is_(None),
            set_={"amount": resource_reservation.c.amount + statement.excluded.amount},
            where=resource_reservation.c.owner_id == statement.excluded.owner_id,
        ).returning(resource_reservation.c.id)
    ).scalar_one_or_none()
    if received is None:
        raise ValueError(f"o detentor {to_kind}/{to_id} pertence a outra conta")


def usage(session: Session, owner_id: uuid.UUID) -> CapacityUsage:
    """Armazenamento reservado pela conta; sem pool ainda, zero sob o teto padrão."""
    row = session.execute(
        select(capacity_pool.c.reserved, capacity_pool.c.max_amount).where(
            account_pool(owner_id, Resource.STORAGE_BYTES)
        )
    ).one_or_none()
    if row is None:
        return CapacityUsage(storage_reserved_bytes=0, storage_max_bytes=ACCOUNT_STORAGE_MAX_BYTES)
    return CapacityUsage(storage_reserved_bytes=row.reserved, storage_max_bytes=row.max_amount)


@dataclass(frozen=True)
class GlobalUsage:
    storage_reserved_bytes: int
    storage_max_bytes: int


def global_usage(session: Session) -> GlobalUsage:
    """Armazenamento reservado no ambiente e o teto, para o alerta de capacidade (3.7)."""
    reserved, max_amount = session.execute(
        select(capacity_pool.c.reserved, capacity_pool.c.max_amount).where(
            global_pool(Resource.STORAGE_BYTES)
        )
    ).one()
    return GlobalUsage(storage_reserved_bytes=reserved, storage_max_bytes=max_amount)


def delete_released_reservations(session: Session, now: datetime, limit: int) -> int:
    """Apaga até `limit` reservas liberadas há 90 dias ou mais; as vivas nunca.

    O pool não muda: ele soma só as vivas, e a reconciliação recalcula a partir delas.
    """
    chosen = (
        select(resource_reservation.c.id)
        .where(resource_reservation.c.released_at <= now - RELEASED_RETENTION)
        .order_by(resource_reservation.c.released_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
        .scalar_subquery()
    )
    removed = session.execute(
        delete(resource_reservation)
        .where(resource_reservation.c.id.in_(chosen))
        .returning(resource_reservation.c.id)
    ).all()
    return len(removed)


def has_live_reservation(
    holder: HolderKind, holder_id: ColumnElement[uuid.UUID]
) -> ColumnElement[bool]:
    """`EXISTS` de reserva viva do detentor, para outro módulo filtrar sem ler esta tabela."""
    return exists().where(
        resource_reservation.c.holder_kind == holder,
        resource_reservation.c.holder_id == holder_id,
        resource_reservation.c.released_at.is_(None),
    )


def purge_owner(session: Session, owner_id: uuid.UUID) -> None:
    """Apaga o pool e as reservas já liberadas da conta, antes da purga de `app_user`.

    Chamada por `identity.purge` (Frente C) depois que todos os objetos da conta foram
    removidos; reserva ainda viva é estado impossível nesse ponto e levanta erro.
    """
    alive = session.execute(
        select(func.count())
        .select_from(resource_reservation)
        .where(
            resource_reservation.c.owner_id == owner_id,
            resource_reservation.c.released_at.is_(None),
        )
    ).scalar_one()
    if alive:
        raise RuntimeError(f"conta {owner_id} ainda tem {alive} reserva(s) viva(s)")
    session.execute(delete(resource_reservation).where(resource_reservation.c.owner_id == owner_id))
    session.execute(
        delete(capacity_pool).where(
            capacity_pool.c.scope == _ACCOUNT, capacity_pool.c.owner_id == owner_id
        )
    )


def set_account_storage_max(session: Session, owner_id: uuid.UUID, max_bytes: int | None) -> None:
    """Muda o teto de armazenamento da conta (`gs-admin set-limits`); `None` volta ao padrão.

    Trava o pool e confere o reservado antes: abaixo dele, levanta `StorageBelowReservedError` sem
    mudar nada. O pool global não muda; a soma dos tetos das contas pode passar dele de propósito.
    """
    target = ACCOUNT_STORAGE_MAX_BYTES if max_bytes is None else max_bytes
    if target <= 0:
        raise ValueError(f"o teto precisa ser positivo: {target}")
    ensure_account_pool(session, owner_id, Resource.STORAGE_BYTES)
    reserved = session.execute(
        select(capacity_pool.c.reserved)
        .where(account_pool(owner_id, Resource.STORAGE_BYTES))
        .with_for_update()
    ).scalar_one()
    if reserved > target:
        raise StorageBelowReservedError(f"a conta já reservou {reserved} bytes; teto {target}")
    session.execute(
        update(capacity_pool)
        .where(account_pool(owner_id, Resource.STORAGE_BYTES))
        .values(max_amount=target)
    )


def ensure_account_pool(session: Session, owner_id: uuid.UUID, resource: Resource) -> None:
    """Cria o pool da conta sob demanda; concorrente que chegue antes vence sem erro."""
    session.execute(
        insert(capacity_pool)
        .values(
            scope=_ACCOUNT, owner_id=owner_id, resource=resource, max_amount=ACCOUNT_MAX[resource]
        )
        .on_conflict_do_nothing()
    )


def _take(session: Session, pool: ColumnElement[bool], amount: int) -> bool:
    taken = session.execute(
        update(capacity_pool)
        .where(pool, capacity_pool.c.reserved + amount <= capacity_pool.c.max_amount)
        .values(reserved=capacity_pool.c.reserved + amount)
        .returning(capacity_pool.c.id)
    ).scalar_one_or_none()
    return taken is not None


def _give_back(session: Session, pool: ColumnElement[bool], amount: int) -> None:
    """Devolve ao pool; ficar negativo viola o CHECK, sinal de livro-razão divergente."""
    session.execute(
        update(capacity_pool).where(pool).values(reserved=capacity_pool.c.reserved - amount)
    )


def account_pool(owner_id: uuid.UUID, resource: Resource) -> ColumnElement[bool]:
    return and_(
        capacity_pool.c.scope == _ACCOUNT,
        capacity_pool.c.owner_id == owner_id,
        capacity_pool.c.resource == resource,
    )


def global_pool(resource: Resource) -> ColumnElement[bool]:
    return and_(capacity_pool.c.scope == _GLOBAL, capacity_pool.c.resource == resource)


def _require_global_pool(session: Session, resource: Resource) -> None:
    """Sem o pool global (semeado pela migração 0006), a recusa seria um 503 enganoso."""
    found = session.execute(
        select(capacity_pool.c.id).where(global_pool(resource))
    ).scalar_one_or_none()
    if found is None:
        raise RuntimeError(f"pool global de {resource} ausente: a migração 0006 não rodou?")


def _require_non_negative(amount: int) -> None:
    if amount < 0:
        raise ValueError(f"quantidade negativa: {amount}")


def _raise_transfer_refusal(
    session: Session, kind: HolderKind, holder_id: uuid.UUID, amount: int
) -> NoReturn:
    available = session.execute(
        select(resource_reservation.c.amount).where(
            resource_reservation.c.holder_kind == kind,
            resource_reservation.c.holder_id == holder_id,
            resource_reservation.c.released_at.is_(None),
        )
    ).scalar_one_or_none()
    if available is None:
        raise ReservationNotFoundError(f"{kind}/{holder_id} sem reserva viva")
    raise InsufficientReservationError(f"{kind}/{holder_id} tem {available}, pedido {amount}")
