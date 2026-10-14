"""Estado transitório do login no PostgreSQL, como cache do Authlib (ADR 025)."""

import asyncio
import threading
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.identity.login_state import (
    LOGIN_STATE_TTL,
    PostgresLoginStateCache,
    purge_expired_states,
    save_state,
    take_state,
)
from goblinshift.identity.tables import login_state
from goblinshift.platform.db import unit_of_work

pytestmark = pytest.mark.integration

T0 = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)
KEY = "_state_google_abc123"
VALUE = '{"data": {"nonce": "n"}}'


class FakeClock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(T0)


@pytest.fixture
def cache(factory: sessionmaker[Session], clock: FakeClock) -> PostgresLoginStateCache:
    return PostgresLoginStateCache(factory, clock=clock)


def stored_keys(factory: sessionmaker[Session]) -> set[str]:
    with unit_of_work(factory) as session:
        return set(session.execute(select(login_state.c.key)).scalars())


def test_valor_gravado_e_lido_uma_vez(cache: PostgresLoginStateCache) -> None:
    asyncio.run(cache.set(KEY, VALUE, 600))

    assert asyncio.run(cache.get(KEY)) == VALUE
    assert asyncio.run(cache.get(KEY)) is None


def test_chave_desconhecida_devolve_none(cache: PostgresLoginStateCache) -> None:
    assert asyncio.run(cache.get(KEY)) is None


@pytest.mark.parametrize(
    ("elapsed", "found"),
    [
        (LOGIN_STATE_TTL - timedelta(seconds=1), True),
        (LOGIN_STATE_TTL, False),
    ],
    ids=["um-segundo-antes", "no-prazo"],
)
def test_estado_vence_em_dez_minutos(
    cache: PostgresLoginStateCache, clock: FakeClock, elapsed: timedelta, found: bool
) -> None:
    asyncio.run(cache.set(KEY, VALUE, 600))
    clock.now = T0 + elapsed

    assert (asyncio.run(cache.get(KEY)) == VALUE) is found


def test_prazo_pedido_acima_de_dez_minutos_e_limitado(
    cache: PostgresLoginStateCache, clock: FakeClock
) -> None:
    # O Authlib pede 3600 s por padrão; o estado do login nunca vive mais que o cookie.
    asyncio.run(cache.set(KEY, VALUE, 3600))
    clock.now = T0 + LOGIN_STATE_TTL

    assert asyncio.run(cache.get(KEY)) is None


def test_prazo_menor_que_dez_minutos_e_respeitado(
    cache: PostgresLoginStateCache, clock: FakeClock
) -> None:
    asyncio.run(cache.set(KEY, VALUE, 60))
    clock.now = T0 + timedelta(seconds=60)

    assert asyncio.run(cache.get(KEY)) is None


def test_gravar_a_mesma_chave_substitui_o_valor(cache: PostgresLoginStateCache) -> None:
    asyncio.run(cache.set(KEY, "antigo", 600))
    asyncio.run(cache.set(KEY, VALUE, 600))

    assert asyncio.run(cache.get(KEY)) == VALUE


def test_delete_remove_e_repetir_nao_falha(
    cache: PostgresLoginStateCache, factory: sessionmaker[Session]
) -> None:
    asyncio.run(cache.set(KEY, VALUE, 600))

    asyncio.run(cache.delete(KEY))
    asyncio.run(cache.delete(KEY))

    assert stored_keys(factory) == set()


@pytest.mark.parametrize(
    "key",
    ["_state_google_\x00", "_state_google_" + "a" * 300, ""],
    ids=["nul", "longa", "vazia"],
)
def test_chave_que_o_banco_nao_guardaria_e_so_desconhecida(
    cache: PostgresLoginStateCache, key: str
) -> None:
    # O `state` vem da query string do callback: lixo nele não pode virar erro 500.
    assert asyncio.run(cache.get(key)) is None
    asyncio.run(cache.delete(key))


def test_duas_leituras_simultaneas_so_uma_recebe_o_estado(
    factory: sessionmaker[Session],
) -> None:
    with unit_of_work(factory) as session:
        save_state(session, KEY, VALUE, T0 + LOGIN_STATE_TTL)
    barrier = threading.Barrier(2)
    results: list[str | None] = []

    def take() -> None:
        with factory() as session, session.begin():
            barrier.wait()
            results.append(take_state(session, KEY, T0))

    threads = [threading.Thread(target=take) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(results, key=str) == [None, VALUE]


def test_limpeza_remove_so_os_vencidos_e_respeita_o_lote(
    factory: sessionmaker[Session],
) -> None:
    with unit_of_work(factory) as session:
        for index in range(3):
            save_state(session, f"vencido-{index}", VALUE, T0 - timedelta(seconds=index))
        save_state(session, "no-prazo", VALUE, T0)
        save_state(session, "vivo", VALUE, T0 + timedelta(seconds=1))

    with unit_of_work(factory) as session:
        assert purge_expired_states(session, T0, limit=2) == 2
    with unit_of_work(factory) as session:
        assert purge_expired_states(session, T0, limit=10) == 2

    assert stored_keys(factory) == {"vivo"}


def test_limpeza_sem_vencidos_devolve_zero(factory: sessionmaker[Session]) -> None:
    with unit_of_work(factory) as session:
        assert purge_expired_states(session, T0, limit=10) == 0
        assert session.execute(select(func.count()).select_from(login_state)).scalar_one() == 0
