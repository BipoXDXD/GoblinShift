"""Reservas simultâneas no limite (docs/GoblinShiftPlan.md, 1.7; regras de dados 3 e 4).

Cada thread abre a própria conexão e espera numa barreira antes de reservar, para que todas
disputem o mesmo pool ao mesmo tempo. O teto vale pelo `UPDATE` condicional: passam exatamente as
reservas que cabem, e a soma nunca ultrapassa o máximo.
"""

import threading
import uuid
from collections.abc import Callable
from functools import partial

import pytest
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.capacity import (
    AccountCapacityExceededError,
    GlobalCapacityExceededError,
    HolderKind,
    release,
)
from goblinshift.platform.db import unit_of_work
from tests.capacity.support import (
    account_reserved,
    create_account_pool,
    global_reserved,
    live_sum,
    new_owner,
    reserve_now,
    set_global_max,
)

pytestmark = pytest.mark.integration

THREADS = 8
JOIN_TIMEOUT_S = 30.0


def run_together(actions: list[Callable[[], object]]) -> list[Exception | None]:
    """Roda as ações em threads soltas ao mesmo tempo; devolve a exceção de cada uma (ou None)."""
    barrier = threading.Barrier(len(actions))
    outcomes: list[Exception | None] = [None] * len(actions)

    def worker(index: int, action: Callable[[], object]) -> None:
        barrier.wait()
        try:
            action()
        except Exception as error:  # o teste confere cada resultado depois do join
            outcomes[index] = error

    threads = [
        threading.Thread(target=worker, args=(i, action)) for i, action in enumerate(actions)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(JOIN_TIMEOUT_S)
    assert not any(thread.is_alive() for thread in threads), "thread presa: possível deadlock"
    return outcomes


def test_reservas_simultaneas_no_teto_da_conta_passam_so_as_que_cabem(
    factory: sessionmaker[Session],
) -> None:
    owner = new_owner(factory)
    create_account_pool(factory, owner, 300)

    outcomes = run_together([lambda: reserve_now(factory, owner, 100) for _ in range(THREADS)])

    accepted = [o for o in outcomes if o is None]
    refused = [o for o in outcomes if o is not None]
    assert len(accepted) == 3
    assert all(isinstance(error, AccountCapacityExceededError) for error in refused)
    assert account_reserved(factory, owner) == 300 == live_sum(factory, owner)
    assert global_reserved(factory) == 300


def test_reservas_simultaneas_de_contas_diferentes_no_teto_global(
    factory: sessionmaker[Session],
) -> None:
    set_global_max(factory, 500)
    owners = [new_owner(factory) for _ in range(THREADS)]

    outcomes = run_together([partial(reserve_now, factory, o, 100) for o in owners])

    accepted = [o for o in outcomes if o is None]
    refused = [o for o in outcomes if o is not None]
    assert len(accepted) == 5
    assert all(isinstance(error, GlobalCapacityExceededError) for error in refused)
    assert global_reserved(factory) == 500 == live_sum(factory)
    # A conta recusada pelo global não fica com o incremento: a transação inteira desfaz.
    assert sum(account_reserved(factory, o) or 0 for o in owners) == 500


def test_reservas_e_liberacoes_misturadas_nao_travam_nem_divergem(
    factory: sessionmaker[Session],
) -> None:
    owners = [new_owner(factory) for _ in range(2)]
    held = [reserve_now(factory, owners[i % 2], 10) for i in range(THREADS)]

    def release_one(holder_id: uuid.UUID) -> None:
        with unit_of_work(factory) as session:
            release(session, holder=HolderKind.UPLOAD, holder_id=holder_id)

    actions: list[Callable[[], object]] = [partial(release_one, h) for h in held]
    actions += [partial(reserve_now, factory, owners[i % 2], 5) for i in range(THREADS)]

    outcomes = run_together(actions)

    assert outcomes == [None] * len(actions)
    for owner in owners:
        assert account_reserved(factory, owner) == live_sum(factory, owner) == 20
    assert global_reserved(factory) == live_sum(factory) == 40
