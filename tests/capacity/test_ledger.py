"""Livro-razão de capacidade com PostgreSQL real (docs/GoblinShiftPlan.md, 1.7 e 3.3; ADR 023)."""

import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.capacity import (
    ACCOUNT_STORAGE_MAX_BYTES,
    AccountCapacityExceededError,
    CapacityUsage,
    GlobalCapacityExceededError,
    HolderKind,
    InsufficientReservationError,
    ReservationNotFoundError,
    has_live_reservation,
    purge_owner,
    release,
    release_many,
    reserve_many,
    transfer,
    usage,
)
from goblinshift.capacity.tables import capacity_pool, resource_reservation
from goblinshift.platform.db import unit_of_work
from tests.capacity.support import (
    STORAGE,
    account_reserved,
    create_account_pool,
    global_reserved,
    live_amount,
    new_owner,
    reserve_now,
    set_global_max,
)

pytestmark = pytest.mark.integration

MIB = 1024**2


# --- reserve ---------------------------------------------------------------------------------


def test_primeira_reserva_cria_o_pool_da_conta_com_4_gib(factory: sessionmaker[Session]) -> None:
    owner = new_owner(factory)

    holder_id = reserve_now(factory, owner, 10 * MIB)

    with factory() as session:
        pool = session.execute(
            select(capacity_pool.c.max_amount, capacity_pool.c.reserved).where(
                capacity_pool.c.owner_id == owner
            )
        ).one()
    assert pool.max_amount == ACCOUNT_STORAGE_MAX_BYTES == 4 * 1024**3
    assert pool.reserved == 10 * MIB
    assert global_reserved(factory) == 10 * MIB
    assert live_amount(factory, HolderKind.UPLOAD, holder_id) == 10 * MIB


def test_reservas_seguintes_somam_no_mesmo_pool(factory: sessionmaker[Session]) -> None:
    owner = new_owner(factory)

    reserve_now(factory, owner, 3)
    reserve_now(factory, owner, 4)

    assert account_reserved(factory, owner) == 7
    assert global_reserved(factory) == 7


def test_reserva_que_completa_o_teto_da_conta_e_aceita(factory: sessionmaker[Session]) -> None:
    owner = new_owner(factory)
    create_account_pool(factory, owner, 100)
    reserve_now(factory, owner, 60)

    reserve_now(factory, owner, 40)

    assert account_reserved(factory, owner) == 100


def test_reserva_um_byte_acima_do_teto_da_conta_e_recusada(
    factory: sessionmaker[Session],
) -> None:
    owner = new_owner(factory)
    create_account_pool(factory, owner, 100)
    reserve_now(factory, owner, 60)

    with pytest.raises(AccountCapacityExceededError):
        reserve_now(factory, owner, 41)

    assert account_reserved(factory, owner) == 60
    assert global_reserved(factory) == 60


def test_reserva_acima_do_teto_global_e_recusada_e_a_conta_nao_muda(
    factory: sessionmaker[Session],
) -> None:
    owner, other = new_owner(factory), new_owner(factory)
    set_global_max(factory, 100)
    reserve_now(factory, other, 70)

    with pytest.raises(GlobalCapacityExceededError):
        reserve_now(factory, owner, 31)

    assert account_reserved(factory, owner) in (None, 0)
    assert global_reserved(factory) == 70


def test_reserva_que_completa_o_teto_global_e_aceita(factory: sessionmaker[Session]) -> None:
    set_global_max(factory, 100)
    reserve_now(factory, new_owner(factory), 70)

    reserve_now(factory, new_owner(factory), 30)

    assert global_reserved(factory) == 100


def test_reserva_de_zero_bytes_e_registrada(factory: sessionmaker[Session]) -> None:
    owner = new_owner(factory)

    holder_id = reserve_now(factory, owner, 0)

    assert live_amount(factory, HolderKind.UPLOAD, holder_id) == 0
    assert account_reserved(factory, owner) == 0


def test_reserva_negativa_e_recusada(factory: sessionmaker[Session]) -> None:
    with pytest.raises(ValueError, match="negativ"):
        reserve_now(factory, new_owner(factory), -1)


def test_segunda_reserva_viva_do_mesmo_detentor_e_recusada(
    factory: sessionmaker[Session],
) -> None:
    owner = new_owner(factory)
    holder_id = reserve_now(factory, owner, 5)

    with pytest.raises(IntegrityError, match="resource_reservation_live_holder_idx"):
        reserve_now(factory, owner, 5, holder_id=holder_id)

    assert account_reserved(factory, owner) == 5


def test_pool_global_ausente_e_erro_de_configuracao(factory: sessionmaker[Session]) -> None:
    with unit_of_work(factory) as session:
        session.execute(capacity_pool.delete().where(capacity_pool.c.scope == "global"))

    with pytest.raises(RuntimeError, match="global"):
        reserve_now(factory, new_owner(factory), 1)


# --- release ---------------------------------------------------------------------------------


def test_release_devolve_a_quantidade_e_libera_os_pools(factory: sessionmaker[Session]) -> None:
    owner = new_owner(factory)
    holder_id = reserve_now(factory, owner, 9)

    with unit_of_work(factory) as session:
        released = release(session, holder=HolderKind.UPLOAD, holder_id=holder_id)

    assert released == 9
    assert account_reserved(factory, owner) == 0
    assert global_reserved(factory) == 0
    assert live_amount(factory, HolderKind.UPLOAD, holder_id) is None


def test_release_repetido_devolve_zero(factory: sessionmaker[Session]) -> None:
    owner = new_owner(factory)
    holder_id = reserve_now(factory, owner, 9)
    with unit_of_work(factory) as session:
        release(session, holder=HolderKind.UPLOAD, holder_id=holder_id)

    with unit_of_work(factory) as session:
        again = release(session, holder=HolderKind.UPLOAD, holder_id=holder_id)

    assert again == 0
    assert account_reserved(factory, owner) == 0


def test_release_de_detentor_desconhecido_devolve_zero(factory: sessionmaker[Session]) -> None:
    with unit_of_work(factory) as session:
        assert release(session, holder=HolderKind.RUN, holder_id=uuid.uuid4()) == 0


def test_release_confere_o_tipo_do_detentor(factory: sessionmaker[Session]) -> None:
    owner = new_owner(factory)
    holder_id = reserve_now(factory, owner, 9, holder=HolderKind.UPLOAD)

    with unit_of_work(factory) as session:
        released = release(session, holder=HolderKind.OBJECT, holder_id=holder_id)

    assert released == 0
    assert account_reserved(factory, owner) == 9


def test_release_many_libera_varias_contas_de_uma_vez(factory: sessionmaker[Session]) -> None:
    alice, bob = new_owner(factory), new_owner(factory)
    a1 = reserve_now(factory, alice, 1)
    a2 = reserve_now(factory, alice, 2)
    b1 = reserve_now(factory, bob, 4)
    kept = reserve_now(factory, bob, 8)

    with unit_of_work(factory) as session:
        released = release_many(session, holder=HolderKind.UPLOAD, holder_ids=[b1, a2, a1])

    assert released == 7
    assert account_reserved(factory, alice) == 0
    assert account_reserved(factory, bob) == 8
    assert global_reserved(factory) == 8
    assert live_amount(factory, HolderKind.UPLOAD, kept) == 8


def test_release_many_sem_detentores_devolve_zero(factory: sessionmaker[Session]) -> None:
    with unit_of_work(factory) as session:
        assert release_many(session, holder=HolderKind.UPLOAD, holder_ids=[]) == 0


# --- transfer --------------------------------------------------------------------------------


def test_transfer_parcial_move_a_quantidade_sem_tocar_os_pools(
    factory: sessionmaker[Session],
) -> None:
    owner = new_owner(factory)
    run_id = reserve_now(factory, owner, 100, holder=HolderKind.RUN)
    object_id = uuid.uuid4()

    with unit_of_work(factory) as session:
        transfer(
            session,
            from_holder=(HolderKind.RUN, run_id),
            to_holder=(HolderKind.OBJECT, object_id),
            amount=30,
        )

    assert live_amount(factory, HolderKind.RUN, run_id) == 70
    assert live_amount(factory, HolderKind.OBJECT, object_id) == 30
    assert account_reserved(factory, owner) == 100
    assert global_reserved(factory) == 100


def test_transfer_total_encerra_o_detentor_de_origem(factory: sessionmaker[Session]) -> None:
    owner = new_owner(factory)
    upload_id = reserve_now(factory, owner, 12)
    object_id = uuid.uuid4()

    with unit_of_work(factory) as session:
        transfer(
            session,
            from_holder=(HolderKind.UPLOAD, upload_id),
            to_holder=(HolderKind.OBJECT, object_id),
            amount=12,
        )
        released = release(session, holder=HolderKind.UPLOAD, holder_id=upload_id)

    assert released == 0
    assert live_amount(factory, HolderKind.UPLOAD, upload_id) is None
    assert live_amount(factory, HolderKind.OBJECT, object_id) == 12
    assert account_reserved(factory, owner) == 12


def test_transfer_para_detentor_vivo_soma_na_reserva_dele(factory: sessionmaker[Session]) -> None:
    owner = new_owner(factory)
    run_id = reserve_now(factory, owner, 100, holder=HolderKind.RUN)
    object_id = reserve_now(factory, owner, 5, holder=HolderKind.OBJECT)

    with unit_of_work(factory) as session:
        transfer(
            session,
            from_holder=(HolderKind.RUN, run_id),
            to_holder=(HolderKind.OBJECT, object_id),
            amount=10,
        )

    assert live_amount(factory, HolderKind.OBJECT, object_id) == 15
    assert account_reserved(factory, owner) == 105


def test_transfer_acima_do_reservado_e_recusado(factory: sessionmaker[Session]) -> None:
    owner = new_owner(factory)
    run_id = reserve_now(factory, owner, 10, holder=HolderKind.RUN)

    with pytest.raises(InsufficientReservationError), unit_of_work(factory) as session:
        transfer(
            session,
            from_holder=(HolderKind.RUN, run_id),
            to_holder=(HolderKind.OBJECT, uuid.uuid4()),
            amount=11,
        )

    assert live_amount(factory, HolderKind.RUN, run_id) == 10


def test_transfer_sem_reserva_de_origem_e_recusado(factory: sessionmaker[Session]) -> None:
    with pytest.raises(ReservationNotFoundError), unit_of_work(factory) as session:
        transfer(
            session,
            from_holder=(HolderKind.UPLOAD, uuid.uuid4()),
            to_holder=(HolderKind.OBJECT, uuid.uuid4()),
            amount=1,
        )


def test_transfer_para_detentor_de_outra_conta_e_recusado(
    factory: sessionmaker[Session],
) -> None:
    alice, bob = new_owner(factory), new_owner(factory)
    run_id = reserve_now(factory, alice, 10, holder=HolderKind.RUN)
    bobs_object = reserve_now(factory, bob, 1, holder=HolderKind.OBJECT)

    with pytest.raises(ValueError, match="outra conta"), unit_of_work(factory) as session:
        transfer(
            session,
            from_holder=(HolderKind.RUN, run_id),
            to_holder=(HolderKind.OBJECT, bobs_object),
            amount=5,
        )

    assert live_amount(factory, HolderKind.OBJECT, bobs_object) == 1
    assert live_amount(factory, HolderKind.RUN, run_id) == 10


def test_transfer_negativo_e_recusado(factory: sessionmaker[Session]) -> None:
    owner = new_owner(factory)
    run_id = reserve_now(factory, owner, 10, holder=HolderKind.RUN)

    with pytest.raises(ValueError, match="negativ"), unit_of_work(factory) as session:
        transfer(
            session,
            from_holder=(HolderKind.RUN, run_id),
            to_holder=(HolderKind.OBJECT, uuid.uuid4()),
            amount=-1,
        )


# --- usage, has_live_reservation, purge_owner ------------------------------------------------


def test_usage_de_conta_sem_pool_e_zero_com_o_teto_padrao(
    factory: sessionmaker[Session],
) -> None:
    with factory() as session:
        result = usage(session, new_owner(factory))

    assert result == CapacityUsage(storage_reserved_bytes=0, storage_max_bytes=4 * 1024**3)


def test_usage_mostra_o_reservado_e_o_teto_do_pool(factory: sessionmaker[Session]) -> None:
    owner = new_owner(factory)
    create_account_pool(factory, owner, 500)
    reserve_now(factory, owner, 123)

    with factory() as session:
        result = usage(session, owner)

    assert result == CapacityUsage(storage_reserved_bytes=123, storage_max_bytes=500)


def test_has_live_reservation_filtra_por_tipo_e_por_reserva_viva(
    factory: sessionmaker[Session],
) -> None:
    owner = new_owner(factory)
    live = reserve_now(factory, owner, 1, holder=HolderKind.OBJECT)
    released = reserve_now(factory, owner, 1, holder=HolderKind.OBJECT)
    other_kind = reserve_now(factory, owner, 1, holder=HolderKind.UPLOAD)
    with unit_of_work(factory) as session:
        release(session, holder=HolderKind.OBJECT, holder_id=released)

    candidates = [live, released, other_kind, uuid.uuid4()]
    with factory() as session:
        values = select(func.unnest(candidates).label("holder_id")).subquery()
        found = set(
            session.execute(
                select(values.c.holder_id).where(
                    has_live_reservation(HolderKind.OBJECT, values.c.holder_id)
                )
            ).scalars()
        )

    assert found == {live}


def test_purge_owner_apaga_o_pool_e_as_reservas_liberadas(
    factory: sessionmaker[Session],
) -> None:
    owner, other = new_owner(factory), new_owner(factory)
    holder_id = reserve_now(factory, owner, 5)
    reserve_now(factory, other, 7)
    with unit_of_work(factory) as session:
        release(session, holder=HolderKind.UPLOAD, holder_id=holder_id)

    with unit_of_work(factory) as session:
        purge_owner(session, owner)

    with factory() as session:
        remaining = session.execute(
            select(func.count())
            .select_from(resource_reservation)
            .where(resource_reservation.c.owner_id == owner)
        ).scalar_one()
    assert remaining == 0
    assert account_reserved(factory, owner) is None
    assert account_reserved(factory, other) == 7
    assert global_reserved(factory) == 7


def test_purge_owner_com_reserva_viva_e_estado_impossivel(
    factory: sessionmaker[Session],
) -> None:
    owner = new_owner(factory)
    reserve_now(factory, owner, 5)

    with pytest.raises(RuntimeError, match="viva"), unit_of_work(factory) as session:
        purge_owner(session, owner)

    assert account_reserved(factory, owner) == 5


def test_purge_owner_sem_pool_nao_falha(factory: sessionmaker[Session]) -> None:
    with unit_of_work(factory) as session:
        purge_owner(session, new_owner(factory))


# --- reserve_many ----------------------------------------------------------------------------


def test_reserve_many_reserva_cada_detentor_e_soma_nos_pools(
    factory: sessionmaker[Session],
) -> None:
    owner = new_owner(factory)
    first, second = uuid.uuid4(), uuid.uuid4()

    with unit_of_work(factory) as session:
        reserve_many(
            session,
            owner_id=owner,
            holder=HolderKind.UPLOAD,
            resource=STORAGE,
            amounts={first: 3, second: 0},
        )

    assert live_amount(factory, HolderKind.UPLOAD, first) == 3
    assert live_amount(factory, HolderKind.UPLOAD, second) == 0
    assert account_reserved(factory, owner) == 3
    assert global_reserved(factory) == 3


def test_reserve_many_acima_do_teto_nao_reserva_nenhum(factory: sessionmaker[Session]) -> None:
    owner = new_owner(factory)
    create_account_pool(factory, owner, 10)
    first, second = uuid.uuid4(), uuid.uuid4()

    with pytest.raises(AccountCapacityExceededError), unit_of_work(factory) as session:
        reserve_many(
            session,
            owner_id=owner,
            holder=HolderKind.UPLOAD,
            resource=STORAGE,
            amounts={first: 6, second: 5},
        )

    assert live_amount(factory, HolderKind.UPLOAD, first) is None
    assert account_reserved(factory, owner) == 0


def test_reserve_many_com_quantidade_negativa_e_recusado(factory: sessionmaker[Session]) -> None:
    with pytest.raises(ValueError, match="negativ"), unit_of_work(factory) as session:
        reserve_many(
            session,
            owner_id=new_owner(factory),
            holder=HolderKind.UPLOAD,
            resource=STORAGE,
            amounts={uuid.uuid4(): 5, uuid.uuid4(): -1},
        )
