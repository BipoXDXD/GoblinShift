"""Propriedades do livro-razão sob sequências de operações (Hypothesis stateful, PostgreSQL real).

Um modelo em memória acompanha as reservas vivas. Depois de cada passo, com qualquer sequência de
`reserve`, `release`, `transfer` e `reconcile`:

- cada pool vale exatamente a soma das reservas vivas do seu escopo;
- `0 <= reserved <= max_amount`;
- liberar de novo devolve 0;
- a reconciliação não encontra nada a corrigir.
"""

import uuid

import pytest
from hypothesis import HealthCheck, settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule, run_state_machine_as_test
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.capacity import (
    AccountCapacityExceededError,
    GlobalCapacityExceededError,
    HolderKind,
    InsufficientReservationError,
    reconcile,
    release,
    reserve,
    transfer,
)
from goblinshift.capacity.tables import capacity_pool, resource_reservation
from goblinshift.platform.db import unit_of_work
from tests.capacity.support import STORAGE, create_account_pool, new_owner, set_global_max
from tests.conftest import restore_capacity_seed

pytestmark = pytest.mark.integration

ACCOUNT_MAX = 60
GLOBAL_MAX = 100
OWNERS = 2
AMOUNTS = st.integers(min_value=0, max_value=70)
KINDS = st.sampled_from(list(HolderKind))

type Holder = tuple[HolderKind, uuid.UUID]


def reset_database(factory: sessionmaker[Session]) -> list[uuid.UUID]:
    with unit_of_work(factory) as session:
        session.execute(text("TRUNCATE app_user CASCADE"))
        restore_capacity_seed(session.connection())
    set_global_max(factory, GLOBAL_MAX)
    owners = [new_owner(factory) for _ in range(OWNERS)]
    for owner in owners:
        create_account_pool(factory, owner, ACCOUNT_MAX)
    return owners


def ledger_machine(factory: sessionmaker[Session]) -> type[RuleBasedStateMachine]:
    class LedgerMachine(RuleBasedStateMachine):
        def __init__(self) -> None:
            super().__init__()
            self.owners = reset_database(factory)
            # Reservas vivas no modelo: detentor → (conta, quantidade). As escolhas seguem a ordem
            # de inserção, nunca `sorted`: o id do detentor é uuid4, e ordenar por ele faria o
            # replay do Hypothesis escolher outro detentor (FlakyStrategyDefinition).
            self.live: dict[Holder, tuple[uuid.UUID, int]] = {}
            self.released: list[Holder] = []

        def account_total(self, owner: uuid.UUID) -> int:
            return sum(amount for o, amount in self.live.values() if o == owner)

        def global_total(self) -> int:
            return sum(amount for _, amount in self.live.values())

        @rule(owner_index=st.integers(0, OWNERS - 1), kind=KINDS, amount=AMOUNTS)
        def reserve(self, owner_index: int, kind: HolderKind, amount: int) -> None:
            owner = self.owners[owner_index]
            holder = (kind, uuid.uuid4())
            expected: type[Exception] | None = None
            if self.account_total(owner) + amount > ACCOUNT_MAX:
                expected = AccountCapacityExceededError
            elif self.global_total() + amount > GLOBAL_MAX:
                expected = GlobalCapacityExceededError
            try:
                with unit_of_work(factory) as session:
                    reserve(
                        session,
                        owner_id=owner,
                        holder=kind,
                        holder_id=holder[1],
                        resource=STORAGE,
                        amount=amount,
                    )
            except (AccountCapacityExceededError, GlobalCapacityExceededError) as error:
                assert type(error) is expected
                return
            assert expected is None
            self.live[holder] = (owner, amount)

        @rule(data=st.data())
        def release_live(self, data: st.DataObject) -> None:
            if not self.live:
                return
            holder = data.draw(st.sampled_from(list(self.live)))
            with unit_of_work(factory) as session:
                released = release(session, holder=holder[0], holder_id=holder[1])
            assert released == self.live.pop(holder)[1]
            self.released.append(holder)

        @rule(data=st.data())
        def release_again_returns_zero(self, data: st.DataObject) -> None:
            if not self.released:
                return
            holder = data.draw(st.sampled_from(self.released))
            with unit_of_work(factory) as session:
                assert release(session, holder=holder[0], holder_id=holder[1]) == 0

        @rule(data=st.data(), amount=AMOUNTS, to_existing=st.booleans(), kind=KINDS)
        def transfer_between_holders(
            self, data: st.DataObject, amount: int, to_existing: bool, kind: HolderKind
        ) -> None:
            if not self.live:
                return
            source = data.draw(st.sampled_from(list(self.live)))
            owner, available = self.live[source]
            same_owner = [h for h, (o, _) in self.live.items() if o == owner and h != source]
            if to_existing and same_owner:
                target = data.draw(st.sampled_from(same_owner))
            else:
                target = (kind, uuid.uuid4())
            try:
                with unit_of_work(factory) as session:
                    transfer(session, from_holder=source, to_holder=target, amount=amount)
            except InsufficientReservationError:
                assert amount > available
                return
            assert amount <= available
            remaining = available - amount
            if remaining:
                self.live[source] = (owner, remaining)
            else:
                del self.live[source]
                self.released.append(source)
            self.live[target] = (owner, self.live.get(target, (owner, 0))[1] + amount)

        @rule()
        def reconcile_finds_nothing(self) -> None:
            with unit_of_work(factory) as session:
                report = reconcile(session, limit=10)
            assert report.pools_corrected == 0
            assert report.objects_adopted == 0

        @invariant()
        def pools_match_the_ledger(self) -> None:
            with factory() as session:
                pools = session.execute(
                    select(capacity_pool.c.owner_id, capacity_pool.c.reserved)
                ).all()
                ledger = dict(
                    session.execute(
                        select(
                            resource_reservation.c.owner_id,
                            func.sum(resource_reservation.c.amount),
                        )
                        .where(resource_reservation.c.released_at.is_(None))
                        .group_by(resource_reservation.c.owner_id)
                    ).all()
                )
            by_owner = {row.owner_id: row.reserved for row in pools}
            assert by_owner[None] == self.global_total() == sum(ledger.values())
            assert 0 <= by_owner[None] <= GLOBAL_MAX
            for owner in self.owners:
                assert by_owner[owner] == self.account_total(owner) == ledger.get(owner, 0)
                assert 0 <= by_owner[owner] <= ACCOUNT_MAX

    return LedgerMachine


def test_livro_razao_mantem_o_invariante_em_qualquer_sequencia(
    factory: sessionmaker[Session],
) -> None:
    run_state_machine_as_test(
        ledger_machine(factory),
        settings=settings(
            max_examples=25,
            stateful_step_count=20,
            deadline=None,
            suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
        ),
    )
