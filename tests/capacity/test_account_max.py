"""Teto de armazenamento por conta, ajustado pelo administrador (1.7; ADR 024; `set-limits`).

O teto da conta mora no pool dela; mudar o teto não toca nas reservas. Abaixo do que a conta já
reservou, o pedido é recusado: o CHECK do pool não deixa `reserved` passar do teto.
"""

import uuid

import pytest
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.capacity import (
    ACCOUNT_STORAGE_MAX_BYTES,
    AccountCapacityExceededError,
    StorageBelowReservedError,
    set_account_storage_max,
    usage,
)
from goblinshift.platform.db import unit_of_work
from tests.capacity.support import new_owner, reserve_now

pytestmark = pytest.mark.integration

MIB = 1024**2


def storage_max(factory: sessionmaker[Session], owner: uuid.UUID) -> int:
    with factory() as session:
        return usage(session, owner).storage_max_bytes


def test_conta_sem_pool_ganha_o_teto_pedido(factory: sessionmaker[Session]) -> None:
    owner = new_owner(factory)

    with unit_of_work(factory) as session:
        set_account_storage_max(session, owner, 8 * MIB)

    assert storage_max(factory, owner) == 8 * MIB


def test_teto_menor_passa_a_recusar_reserva_maior(factory: sessionmaker[Session]) -> None:
    owner = new_owner(factory)
    with unit_of_work(factory) as session:
        set_account_storage_max(session, owner, 8 * MIB)

    reserve_now(factory, owner, 8 * MIB)  # no teto: cabe
    with pytest.raises(AccountCapacityExceededError):
        reserve_now(factory, owner, 1)


def test_none_volta_ao_padrao(factory: sessionmaker[Session]) -> None:
    owner = new_owner(factory)
    with unit_of_work(factory) as session:
        set_account_storage_max(session, owner, 8 * MIB)
    with unit_of_work(factory) as session:
        set_account_storage_max(session, owner, None)

    assert storage_max(factory, owner) == ACCOUNT_STORAGE_MAX_BYTES


@pytest.mark.parametrize(("new_max", "accepted"), [(10 * MIB, True), (10 * MIB - 1, False)])
def test_teto_abaixo_do_reservado_e_recusado(
    factory: sessionmaker[Session], new_max: int, accepted: bool
) -> None:
    owner = new_owner(factory)
    reserve_now(factory, owner, 10 * MIB)

    if accepted:
        with unit_of_work(factory) as session:
            set_account_storage_max(session, owner, new_max)
        assert storage_max(factory, owner) == new_max
    else:
        with pytest.raises(StorageBelowReservedError), unit_of_work(factory) as session:
            set_account_storage_max(session, owner, new_max)
        assert storage_max(factory, owner) == ACCOUNT_STORAGE_MAX_BYTES


def test_teto_zero_e_recusado_antes_do_banco(factory: sessionmaker[Session]) -> None:
    owner = new_owner(factory)

    with pytest.raises(ValueError, match="positivo"), unit_of_work(factory) as session:
        set_account_storage_max(session, owner, 0)
