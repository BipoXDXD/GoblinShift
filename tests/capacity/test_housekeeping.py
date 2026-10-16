"""Limpeza do livro-razão e leitura do pool global para a manutenção (1.7 e 3.7; ADR 024).

Reservas liberadas há 90 dias saem, em lotes; as vivas nunca. O uso do pool global alimenta o
alerta de capacidade do ambiente perto do teto.
"""

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.capacity import (
    GLOBAL_STORAGE_MAX_BYTES,
    RELEASED_RETENTION,
    GlobalUsage,
    HolderKind,
    delete_released_reservations,
    global_usage,
    release,
)
from goblinshift.capacity.tables import resource_reservation
from goblinshift.platform.db import unit_of_work
from goblinshift.runs.store import db_now
from tests.capacity.support import new_owner, reserve_now

pytestmark = pytest.mark.integration

SECOND = timedelta(seconds=1)


def released_at(factory: sessionmaker[Session], holder_id: uuid.UUID, age: timedelta) -> None:
    with unit_of_work(factory) as session:
        release(session, holder=HolderKind.UPLOAD, holder_id=holder_id)
        session.execute(
            update(resource_reservation)
            .where(resource_reservation.c.holder_id == holder_id)
            .values(released_at=func.now() - age)
        )


def reservations(factory: sessionmaker[Session]) -> int:
    with factory() as session:
        return session.execute(select(func.count()).select_from(resource_reservation)).scalar_one()


def test_reserva_liberada_ha_90_dias_sai_e_as_demais_ficam(
    factory: sessionmaker[Session],
) -> None:
    owner = new_owner(factory)
    old = reserve_now(factory, owner, 10)
    recent = reserve_now(factory, owner, 10)
    reserve_now(factory, owner, 10)  # viva
    released_at(factory, old, RELEASED_RETENTION + SECOND)
    released_at(factory, recent, RELEASED_RETENTION - timedelta(minutes=1))

    with unit_of_work(factory) as session:
        removed = delete_released_reservations(session, db_now(session), limit=10)

    assert removed == 1
    assert reservations(factory) == 2


def test_limpeza_do_livro_razao_respeita_o_lote(factory: sessionmaker[Session]) -> None:
    owner = new_owner(factory)
    for _ in range(3):
        released_at(factory, reserve_now(factory, owner, 1), RELEASED_RETENTION + SECOND)

    with unit_of_work(factory) as session:
        first = delete_released_reservations(session, db_now(session), limit=2)
    with unit_of_work(factory) as session:
        second = delete_released_reservations(session, db_now(session), limit=2)

    assert (first, second) == (2, 1)


def test_uso_global_soma_as_reservas_vivas_sob_o_teto(factory: sessionmaker[Session]) -> None:
    reserve_now(factory, new_owner(factory), 300)
    reserve_now(factory, new_owner(factory), 200)

    with factory() as session:
        found = global_usage(session)

    assert found == GlobalUsage(
        storage_reserved_bytes=500, storage_max_bytes=GLOBAL_STORAGE_MAX_BYTES
    )
