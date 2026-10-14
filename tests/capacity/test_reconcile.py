"""Reconciliação dos pools com o livro-razão (docs/GoblinShiftPlan.md, 3.3)."""

import logging
import uuid

import pytest
from sqlalchemy import update
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.capacity import HolderKind, ReconcileReport, Unreserved, reconcile
from goblinshift.capacity.tables import capacity_pool
from goblinshift.platform.db import unit_of_work
from tests.capacity.support import (
    account_reserved,
    create_account_pool,
    global_reserved,
    live_amount,
    new_owner,
    reserve_now,
    set_global_max,
)

pytestmark = pytest.mark.integration

LIMIT = 100


def run_reconcile(
    factory: sessionmaker[Session], unreserved: list[Unreserved] | None = None, limit: int = LIMIT
) -> ReconcileReport:
    with unit_of_work(factory) as session:
        return reconcile(session, limit=limit, unreserved=unreserved or [])


def corrupt_pool(factory: sessionmaker[Session], owner: uuid.UUID | None, reserved: int) -> None:
    scope = (
        capacity_pool.c.scope == "global" if owner is None else capacity_pool.c.owner_id == owner
    )
    with unit_of_work(factory) as session:
        session.execute(update(capacity_pool).where(scope).values(reserved=reserved))


def test_pools_coerentes_nao_mudam(factory: sessionmaker[Session]) -> None:
    owner = new_owner(factory)
    reserve_now(factory, owner, 10)

    report = run_reconcile(factory)

    assert report == ReconcileReport(pools_corrected=0, objects_adopted=0)
    assert account_reserved(factory, owner) == 10
    assert global_reserved(factory) == 10


def test_pool_da_conta_divergente_volta_a_soma_das_reservas_vivas(
    factory: sessionmaker[Session], caplog: pytest.LogCaptureFixture
) -> None:
    owner = new_owner(factory)
    reserve_now(factory, owner, 10)
    corrupt_pool(factory, owner, 77)

    with caplog.at_level(logging.WARNING):
        report = run_reconcile(factory)

    assert report.pools_corrected == 1
    assert account_reserved(factory, owner) == 10
    assert any(
        getattr(r, "fields", {}).get("alert") == "capacity_divergence" for r in caplog.records
    )


def test_pool_global_divergente_e_corrigido(factory: sessionmaker[Session]) -> None:
    reserve_now(factory, new_owner(factory), 10)
    corrupt_pool(factory, None, 0)

    report = run_reconcile(factory)

    assert report.pools_corrected == 1
    assert global_reserved(factory) == 10


def test_adota_objeto_sem_reserva_e_ajusta_os_pools(factory: sessionmaker[Session]) -> None:
    owner = new_owner(factory)
    reserve_now(factory, owner, 10)
    object_id = uuid.uuid4()

    report = run_reconcile(factory, [Unreserved(owner, HolderKind.OBJECT, object_id, 25)])

    assert report == ReconcileReport(pools_corrected=0, objects_adopted=1)
    assert live_amount(factory, HolderKind.OBJECT, object_id) == 25
    assert account_reserved(factory, owner) == 35
    assert global_reserved(factory) == 35


def test_adota_objeto_de_conta_sem_pool(factory: sessionmaker[Session]) -> None:
    owner = new_owner(factory)

    report = run_reconcile(factory, [Unreserved(owner, HolderKind.OBJECT, uuid.uuid4(), 5)])

    assert report.objects_adopted == 1
    assert account_reserved(factory, owner) == 5


def test_nao_adota_detentor_que_ja_tem_reserva(factory: sessionmaker[Session]) -> None:
    owner = new_owner(factory)
    object_id = reserve_now(factory, owner, 10, holder=HolderKind.OBJECT)

    report = run_reconcile(factory, [Unreserved(owner, HolderKind.OBJECT, object_id, 10)])

    assert report.objects_adopted == 0
    assert account_reserved(factory, owner) == 10


def test_adocao_que_nao_cabe_no_teto_da_conta_fica_de_fora(
    factory: sessionmaker[Session],
) -> None:
    owner = new_owner(factory)
    create_account_pool(factory, owner, 30)
    reserve_now(factory, owner, 20)
    fits, too_big = uuid.uuid4(), uuid.uuid4()

    report = run_reconcile(
        factory,
        [
            Unreserved(owner, HolderKind.OBJECT, too_big, 11),
            Unreserved(owner, HolderKind.OBJECT, fits, 10),
        ],
    )

    assert report == ReconcileReport(pools_corrected=0, objects_adopted=1, adoptions_refused=1)
    assert live_amount(factory, HolderKind.OBJECT, too_big) is None
    assert account_reserved(factory, owner) == 30


def test_adocao_que_nao_cabe_no_teto_global_fica_de_fora(
    factory: sessionmaker[Session],
) -> None:
    set_global_max(factory, 15)
    reserve_now(factory, new_owner(factory), 10)
    owner = new_owner(factory)

    report = run_reconcile(factory, [Unreserved(owner, HolderKind.OBJECT, uuid.uuid4(), 6)])

    assert report.adoptions_refused == 1
    assert global_reserved(factory) == 10


def test_limite_corta_as_adocoes_do_lote(factory: sessionmaker[Session]) -> None:
    owner = new_owner(factory)
    candidates = [Unreserved(owner, HolderKind.OBJECT, uuid.uuid4(), 1) for _ in range(3)]

    report = run_reconcile(factory, candidates, limit=2)

    assert report.objects_adopted == 2
    assert account_reserved(factory, owner) == 2
