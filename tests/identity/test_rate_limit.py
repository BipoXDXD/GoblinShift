"""Limite de requisições por conta e classe de rota (docs/GoblinShiftPlan.md, 1.7; ADR 028).

Janela fixa de um minuto, contada no PostgreSQL por um `INSERT … ON CONFLICT DO UPDATE` condicional:
vale entre réplicas e nunca passa do limite, nem com requisições simultâneas.
"""

import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta, timezone
from typing import get_args
from uuid import UUID

import pytest
from sqlalchemy import Table, func, insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.identity.rate_limit import (
    REQUESTS_PER_MINUTE,
    RequestChannel,
    RequestClass,
    RequestLimitExceededError,
    count_request,
    count_windows,
    delete_expired_windows,
)
from goblinshift.identity.tables import channel_request_window, legacy_request_window
from goblinshift.platform.db import unit_of_work
from tests.identity.support import new_user
from tests.lock_support import InThread, in_thread, wait_until_someone_waits_for_a_lock

pytestmark = pytest.mark.integration

# 12:00:15,2: faltam 44,8 s para a janela das 12:00 terminar.
NOW = datetime(2026, 10, 14, 12, 0, 15, 200_000, tzinfo=UTC)
WINDOW_START = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)
JOIN_TIMEOUT_S = 30.0


def count(
    factory: sessionmaker[Session],
    owner: UUID,
    request_class: RequestClass = RequestClass.READ,
    now: datetime = NOW,
    channel: RequestChannel = "session",
) -> None:
    with unit_of_work(factory) as session:
        count_request(session, owner, channel, request_class, now)


def count_many(
    factory: sessionmaker[Session],
    owner: UUID,
    request_class: RequestClass,
    times: int,
    channel: RequestChannel = "session",
) -> None:
    for _ in range(times):
        count(factory, owner, request_class, channel=channel)


def stored_count(
    factory: sessionmaker[Session],
    owner: UUID,
    request_class: RequestClass,
    channel: RequestChannel = "session",
) -> int:
    with factory() as session:
        total: int = session.execute(
            select(func.coalesce(func.sum(channel_request_window.c.request_count), 0)).where(
                channel_request_window.c.owner_id == owner,
                channel_request_window.c.channel == channel,
                channel_request_window.c.request_class == request_class.value,
            )
        ).scalar_one()
    return total


def test_limites_por_minuto_sao_os_do_plano() -> None:
    assert REQUESTS_PER_MINUTE == {
        RequestClass.READ: 120,
        RequestClass.COMMAND: 60,
        RequestClass.RUN_CREATE: 10,
    }


@pytest.mark.parametrize("request_class", list(RequestClass))
def test_ultima_requisicao_do_limite_passa_e_a_seguinte_e_recusada(
    factory: sessionmaker[Session], request_class: RequestClass
) -> None:
    owner = new_user(factory)
    limit = REQUESTS_PER_MINUTE[request_class]
    count_many(factory, owner, request_class, limit - 1)

    count(factory, owner, request_class)  # a de número `limit` ainda passa
    with pytest.raises(RequestLimitExceededError):
        count(factory, owner, request_class)

    assert stored_count(factory, owner, request_class) == limit


def test_recusa_informa_o_tempo_ate_o_fim_da_janela(factory: sessionmaker[Session]) -> None:
    owner = new_user(factory)
    count_many(factory, owner, RequestClass.RUN_CREATE, 10)

    with pytest.raises(RequestLimitExceededError) as refused:
        count(factory, owner, RequestClass.RUN_CREATE)

    assert refused.value.retry_after == timedelta(seconds=44, microseconds=800_000)


def test_recusa_no_ultimo_microssegundo_da_janela_espera_um_microssegundo(
    factory: sessionmaker[Session],
) -> None:
    owner = new_user(factory)
    last_instant = WINDOW_START + timedelta(minutes=1) - timedelta(microseconds=1)
    for _ in range(10):
        count(factory, owner, RequestClass.RUN_CREATE, last_instant)

    with pytest.raises(RequestLimitExceededError) as refused:
        count(factory, owner, RequestClass.RUN_CREATE, last_instant)

    assert refused.value.retry_after == timedelta(microseconds=1)


def test_janela_seguinte_comeca_do_zero(factory: sessionmaker[Session]) -> None:
    owner = new_user(factory)
    count_many(factory, owner, RequestClass.RUN_CREATE, 10)

    count(factory, owner, RequestClass.RUN_CREATE, WINDOW_START + timedelta(minutes=1))

    with factory() as session:
        windows = session.execute(
            select(channel_request_window.c.window_start, channel_request_window.c.request_count)
            .where(channel_request_window.c.owner_id == owner)
            .order_by(channel_request_window.c.window_start)
        ).all()
    assert [tuple(w) for w in windows] == [
        (WINDOW_START, 10),
        (WINDOW_START + timedelta(minutes=1), 1),
    ]


def test_janela_e_o_minuto_em_utc_mesmo_com_relogio_em_outro_fuso(
    factory: sessionmaker[Session],
) -> None:
    owner = new_user(factory)
    sao_paulo = NOW.astimezone(timezone(timedelta(hours=-3)))

    count(factory, owner, now=sao_paulo)

    with factory() as session:
        start = session.execute(
            select(channel_request_window.c.window_start).where(
                channel_request_window.c.owner_id == owner
            )
        ).scalar_one()
    assert start == WINDOW_START


def test_contas_tem_limites_independentes(factory: sessionmaker[Session]) -> None:
    first, second = new_user(factory), new_user(factory)
    count_many(factory, first, RequestClass.RUN_CREATE, 10)

    count(factory, second, RequestClass.RUN_CREATE)

    assert stored_count(factory, second, RequestClass.RUN_CREATE) == 1


def test_classes_tem_limites_independentes(factory: sessionmaker[Session]) -> None:
    owner = new_user(factory)
    count_many(factory, owner, RequestClass.RUN_CREATE, 10)

    count(factory, owner, RequestClass.COMMAND)

    assert stored_count(factory, owner, RequestClass.COMMAND) == 1


def test_janelas_de_canais_diferentes_sao_independentes(factory: sessionmaker[Session]) -> None:
    # A chave que esgota a conta não tranca a sessão do titular (#442, 49a), e vice-versa.
    owner = new_user(factory)
    count_many(factory, owner, RequestClass.RUN_CREATE, 10, channel="api_key")

    count(factory, owner, RequestClass.RUN_CREATE, channel="session")

    with pytest.raises(RequestLimitExceededError):
        count(factory, owner, RequestClass.RUN_CREATE, channel="api_key")
    assert stored_count(factory, owner, RequestClass.RUN_CREATE, "api_key") == 10
    assert stored_count(factory, owner, RequestClass.RUN_CREATE, "session") == 1


def run_together(actions: list[Callable[[], object]]) -> list[Exception | None]:
    """Roda as ações em threads ao mesmo tempo; devolve a exceção de cada uma (ou None)."""
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


@pytest.mark.parametrize(
    ("already_counted", "simultaneous", "accepted"),
    [
        (9, 2, 1),  # duas na fronteira: uma só passa
        (0, 16, 10),  # janela nova disputada: a primeira inserção também é atômica
    ],
)
def test_requisicoes_simultaneas_nao_passam_do_limite(
    factory: sessionmaker[Session], already_counted: int, simultaneous: int, accepted: int
) -> None:
    owner = new_user(factory)
    count_many(factory, owner, RequestClass.RUN_CREATE, already_counted)

    outcomes = run_together(
        [lambda: count(factory, owner, RequestClass.RUN_CREATE) for _ in range(simultaneous)]
    )

    assert sum(outcome is None for outcome in outcomes) == accepted
    refused = [outcome for outcome in outcomes if outcome is not None]
    assert all(isinstance(error, RequestLimitExceededError) for error in refused)
    assert stored_count(factory, owner, RequestClass.RUN_CREATE) == 10


def count_while_first_holds(factory: sessionmaker[Session], owner: UUID) -> InThread[None]:
    """A segunda contagem começa com a primeira sem commit e espera por ela."""
    with factory() as first, first.begin():
        count_request(first, owner, "session", RequestClass.RUN_CREATE, NOW)
        second = in_thread(lambda: count(factory, owner, RequestClass.RUN_CREATE))
        wait_until_someone_waits_for_a_lock(factory, "a segunda contagem")
    return second


def test_contagem_concorrente_na_fronteira_espera_e_ve_a_decima(
    factory: sessionmaker[Session],
) -> None:
    owner = new_user(factory)
    count_many(factory, owner, RequestClass.RUN_CREATE, 9)

    second = count_while_first_holds(factory, owner)

    with pytest.raises(RequestLimitExceededError):
        second.result()

    assert stored_count(factory, owner, RequestClass.RUN_CREATE) == 10


def test_contagem_concorrente_em_janela_nova_espera_a_insercao_e_soma(
    factory: sessionmaker[Session],
) -> None:
    owner = new_user(factory)

    count_while_first_holds(factory, owner).result()

    assert stored_count(factory, owner, RequestClass.RUN_CREATE) == 2


def insert_window(
    factory: sessionmaker[Session],
    owner: UUID,
    start: datetime,
    table: Table = channel_request_window,
) -> None:
    values: dict[str, object] = {
        "owner_id": owner,
        "request_class": "read",
        "window_start": start,
        "request_count": 1,
    }
    if table is channel_request_window:
        values["channel"] = "session"
    with unit_of_work(factory) as session:
        session.execute(insert(table).values(**values))


def test_limpeza_remove_so_janelas_terminadas_ha_mais_de_um_minuto(
    factory: sessionmaker[Session],
) -> None:
    owner = new_user(factory)
    for minutes_ago in (0, 1, 2, 3, 60):
        insert_window(factory, owner, WINDOW_START - timedelta(minutes=minutes_ago))

    with unit_of_work(factory) as session:
        removed = delete_expired_windows(session, WINDOW_START, limit=500)
        remaining = count_windows(session)

    # A janela anterior fica: uma réplica com o relógio um pouco atrasado ainda conta nela.
    assert (removed, remaining) == (3, 2)


def test_limpeza_esvazia_tambem_a_tabela_sem_canal_da_0007(factory: sessionmaker[Session]) -> None:
    # Até o PR que apaga a tabela antiga, a réplica anterior ao deploy ainda pode gravar nela.
    owner = new_user(factory)
    for minutes_ago in (0, 2, 3):
        insert_window(factory, owner, WINDOW_START - timedelta(minutes=minutes_ago))
        insert_window(
            factory, owner, WINDOW_START - timedelta(minutes=minutes_ago), legacy_request_window
        )

    with unit_of_work(factory) as session:
        removed = delete_expired_windows(session, WINDOW_START, limit=500)
        remaining = count_windows(session)

    assert (removed, remaining) == (4, 2)


@pytest.mark.parametrize(
    ("legacy", "current", "limit"),
    [(3, 0, 2), (0, 3, 2), (1, 3, 2)],
    ids=["so_antiga", "so_nova", "as_duas"],
)
def test_limpeza_respeita_o_lote(
    factory: sessionmaker[Session], legacy: int, current: int, limit: int
) -> None:
    owner = new_user(factory)
    for minutes_ago in range(2, 2 + legacy):
        insert_window(
            factory, owner, WINDOW_START - timedelta(minutes=minutes_ago), legacy_request_window
        )
    for minutes_ago in range(2, 2 + current):
        insert_window(factory, owner, WINDOW_START - timedelta(minutes=minutes_ago))

    with unit_of_work(factory) as session:
        removed = delete_expired_windows(session, WINDOW_START, limit=limit)

    assert removed == limit


def test_limpeza_sem_janelas_nao_remove_nada(factory: sessionmaker[Session]) -> None:
    with unit_of_work(factory) as session:
        assert delete_expired_windows(session, WINDOW_START, limit=500) == 0
        assert count_windows(session) == 0


@pytest.mark.parametrize(
    "values",
    [
        {"channel": "session", "request_class": "transfer", "request_count": 1},
        {"channel": "session", "request_class": "read", "request_count": 0},
        {"channel": "cookie", "request_class": "read", "request_count": 1},
    ],
    ids=["classe", "contagem", "canal"],
)
def test_banco_recusa_classe_ou_canal_desconhecido_e_contagem_nao_positiva(
    factory: sessionmaker[Session], values: dict[str, object]
) -> None:
    owner = new_user(factory)
    with pytest.raises(IntegrityError), unit_of_work(factory) as session:
        session.execute(
            insert(channel_request_window).values(
                owner_id=owner, window_start=WINDOW_START, **values
            )
        )


def test_check_do_banco_lista_as_classes_e_os_canais_do_codigo(
    factory: sessionmaker[Session],
) -> None:
    owner = new_user(factory)
    for channel in get_args(RequestChannel.__value__):
        for request_class in RequestClass:
            with unit_of_work(factory) as session:
                session.execute(
                    insert(channel_request_window).values(
                        owner_id=owner,
                        channel=channel,
                        request_class=request_class.value,
                        window_start=WINDOW_START,
                        request_count=1,
                    )
                )
