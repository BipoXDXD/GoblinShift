"""Vagas de transferência simultânea entre réplicas (1.7: 2 por conta, 4 no ambiente)."""

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.files.errors import TransferLimitReachedError
from goblinshift.files.limits import TRANSFER_MAX_DURATION
from goblinshift.files.states import TransferDirection
from goblinshift.files.tables import transfer_slot
from goblinshift.files.transfers import acquire_transfer_slot, release_transfer_slot
from goblinshift.platform.db import unit_of_work
from tests.files.support import create_user
from tests.lock_support import in_thread, wait_until_someone_waits_for_a_lock

pytestmark = pytest.mark.integration

UPLOAD = TransferDirection.UPLOAD
DOWNLOAD = TransferDirection.DOWNLOAD


def acquire(
    factory: sessionmaker[Session], owner: uuid.UUID, direction: TransferDirection = UPLOAD
) -> uuid.UUID:
    with unit_of_work(factory) as session:
        return acquire_transfer_slot(session, owner, direction)


def slot_count(factory: sessionmaker[Session]) -> int:
    with factory() as session:
        count: int = session.execute(select(func.count()).select_from(transfer_slot)).scalar_one()
    return count


def test_conta_ocupa_duas_vagas_e_a_terceira_e_recusada(factory: sessionmaker[Session]) -> None:
    owner = create_user(factory, "a")
    acquire(factory, owner)
    acquire(factory, owner)

    with pytest.raises(TransferLimitReachedError):
        acquire(factory, owner)

    assert slot_count(factory) == 2


def test_ambiente_aceita_quatro_transferencias_e_recusa_a_quinta(
    factory: sessionmaker[Session],
) -> None:
    owners = [create_user(factory, name) for name in ("a", "b", "c")]
    acquire(factory, owners[0])
    acquire(factory, owners[0])
    acquire(factory, owners[1])
    acquire(factory, owners[1])

    with pytest.raises(TransferLimitReachedError):
        acquire(factory, owners[2])


def test_uploads_e_downloads_tem_vagas_separadas(factory: sessionmaker[Session]) -> None:
    owner = create_user(factory, "a")
    acquire(factory, owner, UPLOAD)
    acquire(factory, owner, UPLOAD)

    acquire(factory, owner, DOWNLOAD)

    assert slot_count(factory) == 3


def test_liberar_devolve_a_vaga(factory: sessionmaker[Session]) -> None:
    owner = create_user(factory, "a")
    first = acquire(factory, owner)
    acquire(factory, owner)

    with unit_of_work(factory) as session:
        release_transfer_slot(session, first)

    acquire(factory, owner)
    assert slot_count(factory) == 2


def test_liberar_duas_vezes_e_sucesso(factory: sessionmaker[Session]) -> None:
    owner = create_user(factory, "a")
    slot = acquire(factory, owner)

    for _ in range(2):
        with unit_of_work(factory) as session:
            release_transfer_slot(session, slot)

    assert slot_count(factory) == 0


def test_vaga_vencida_de_replica_que_caiu_nao_bloqueia_e_e_removida(
    factory: sessionmaker[Session],
) -> None:
    owner = create_user(factory, "a")
    stale = acquire(factory, owner)
    acquire(factory, owner)
    with unit_of_work(factory) as session:
        session.execute(
            update(transfer_slot)
            .where(transfer_slot.c.id == stale)
            .values(expires_at=func.now() - timedelta(seconds=1))
        )

    acquire(factory, owner)

    with factory() as session:
        ids = set(session.execute(select(transfer_slot.c.id)).scalars())
    assert stale not in ids
    assert len(ids) == 2


def test_vaga_dura_mais_que_a_transferencia_mais_longa(factory: sessionmaker[Session]) -> None:
    owner = create_user(factory, "a")
    slot = acquire(factory, owner)

    with factory() as session:
        lease = session.execute(
            select(transfer_slot.c.expires_at - transfer_slot.c.acquired_at).where(
                transfer_slot.c.id == slot
            )
        ).scalar_one()
    assert lease > TRANSFER_MAX_DURATION


def test_pedido_concorrente_espera_a_trava_e_ve_a_segunda_vaga(
    factory: sessionmaker[Session],
) -> None:
    owner = create_user(factory, "a")
    acquire(factory, owner)

    with factory() as first, first.begin():
        acquire_transfer_slot(first, owner, UPLOAD)
        second = in_thread(lambda: acquire(factory, owner))
        wait_until_someone_waits_for_a_lock(factory, "o segundo pedido de vaga")

    with pytest.raises(TransferLimitReachedError):
        second.result()
    assert slot_count(factory) == 2
