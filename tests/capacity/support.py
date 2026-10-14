"""Construtores e leituras dos testes do livro-razão de capacidade."""

import uuid

from sqlalchemy import func, insert, select, update
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.capacity import HolderKind, Resource, reserve
from goblinshift.capacity.tables import capacity_pool, resource_reservation
from goblinshift.identity import CURRENT_TERMS_VERSION
from goblinshift.identity.tables import app_user
from goblinshift.platform.db import unit_of_work

STORAGE = Resource.STORAGE_BYTES


def new_owner(factory: sessionmaker[Session]) -> uuid.UUID:
    with unit_of_work(factory) as session:
        owner: uuid.UUID = session.execute(
            insert(app_user)
            .values(
                issuer="test",
                subject=f"conta-{uuid.uuid4()}",
                display_name="Conta",
                # Conta comum já ativa: aceitou os termos vigentes (ADR 029).
                terms_version=CURRENT_TERMS_VERSION,
                terms_accepted_at=func.now(),
            )
            .returning(app_user.c.id)
        ).scalar_one()
    return owner


def set_global_max(factory: sessionmaker[Session], max_amount: int) -> None:
    with unit_of_work(factory) as session:
        session.execute(
            update(capacity_pool)
            .where(capacity_pool.c.scope == "global")
            .values(max_amount=max_amount)
        )


def create_account_pool(factory: sessionmaker[Session], owner: uuid.UUID, max_amount: int) -> None:
    """Pool da conta com teto pequeno, criado antes da primeira reserva (que não o recria)."""
    with unit_of_work(factory) as session:
        session.execute(
            insert(capacity_pool).values(
                scope="account", owner_id=owner, resource=STORAGE.value, max_amount=max_amount
            )
        )


def reserve_now(
    factory: sessionmaker[Session],
    owner: uuid.UUID,
    amount: int,
    holder: HolderKind = HolderKind.UPLOAD,
    holder_id: uuid.UUID | None = None,
) -> uuid.UUID:
    holder_id = holder_id or uuid.uuid4()
    with unit_of_work(factory) as session:
        reserve(
            session,
            owner_id=owner,
            holder=holder,
            holder_id=holder_id,
            resource=STORAGE,
            amount=amount,
        )
    return holder_id


def account_reserved(factory: sessionmaker[Session], owner: uuid.UUID) -> int | None:
    with factory() as session:
        reserved: int | None = session.execute(
            select(capacity_pool.c.reserved).where(
                capacity_pool.c.scope == "account", capacity_pool.c.owner_id == owner
            )
        ).scalar_one_or_none()
    return reserved


def global_reserved(factory: sessionmaker[Session]) -> int:
    with factory() as session:
        reserved: int = session.execute(
            select(capacity_pool.c.reserved).where(capacity_pool.c.scope == "global")
        ).scalar_one()
    return reserved


def live_amount(
    factory: sessionmaker[Session], holder: HolderKind, holder_id: uuid.UUID
) -> int | None:
    with factory() as session:
        amount: int | None = session.execute(
            select(resource_reservation.c.amount).where(
                resource_reservation.c.holder_kind == holder.value,
                resource_reservation.c.holder_id == holder_id,
                resource_reservation.c.released_at.is_(None),
            )
        ).scalar_one_or_none()
    return amount


def live_sum(factory: sessionmaker[Session], owner: uuid.UUID | None = None) -> int:
    statement = select(func.coalesce(func.sum(resource_reservation.c.amount), 0)).where(
        resource_reservation.c.released_at.is_(None)
    )
    if owner is not None:
        statement = statement.where(resource_reservation.c.owner_id == owner)
    with factory() as session:
        total: int = session.execute(statement).scalar_one()
    return total
