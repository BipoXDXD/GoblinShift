"""`Idempotency-Key` opcional em `POST /uploads` (#442, item 57a).

Repetir o pedido com a mesma chave e o mesmo corpo devolve o mesmo lote, sem reservar de novo.
"""

import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from typing import Any

import httpx2 as httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select, text, update
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.capacity.tables import capacity_pool
from goblinshift.files.limits import INCOMPLETE_UPLOAD_TTL, MIN_INTENT_RESERVED_BYTES
from goblinshift.files.tables import upload
from goblinshift.platform.db import unit_of_work
from tests.api.files_support import UPLOADS, new_account, send_content
from tests.api.support import assert_problem
from tests.capacity.support import account_reserved

pytestmark = pytest.mark.integration

KEY = "k" * 16
# Dois arquivos abaixo de 64 KiB: cada intenção reserva o mínimo.
ITEMS = [{"name": "a.csv", "size": 3}, {"name": "b.txt", "size": 5}]
WAIT_DEADLINE_SECONDS = 5.0


def post_uploads(
    client: TestClient, auth: dict[str, str], items: list[dict[str, Any]], key: str | None = None
) -> httpx.Response:
    headers = auth if key is None else {**auth, "Idempotency-Key": key}
    return client.post(UPLOADS, json={"items": items}, headers=headers)


def count_uploads(factory: sessionmaker[Session], owner: uuid.UUID) -> int:
    with factory() as session:
        count: int = session.execute(
            select(func.count()).select_from(upload).where(upload.c.owner_id == owner)
        ).scalar_one()
    return count


def ids_of(response: httpx.Response) -> list[str]:
    return [item["id"] for item in response.json()["items"]]


def test_mesma_chave_e_corpo_cria_um_lote_e_reserva_uma_vez(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    owner, auth = new_account(factory)

    first = post_uploads(client, auth, ITEMS, key=KEY)
    second = post_uploads(client, auth, ITEMS, key=KEY)

    assert first.status_code == second.status_code == 201, second.text
    assert ids_of(second) == ids_of(first)
    assert [i["content"]["url"] for i in second.json()["items"]] == [
        i["content"]["url"] for i in first.json()["items"]
    ]
    assert "Idempotency-Replayed" not in first.headers
    assert second.headers["Idempotency-Replayed"] == "true"
    assert count_uploads(factory, owner) == 2
    assert account_reserved(factory, owner) == 2 * MIN_INTENT_RESERVED_BYTES


def test_mesma_chave_com_outro_corpo_e_422(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    owner, auth = new_account(factory)
    post_uploads(client, auth, ITEMS, key=KEY)

    other_body = post_uploads(client, auth, [{"name": "a.csv", "size": 4}], key=KEY)

    assert_problem(other_body, 422, "IDEMPOTENCY_KEY_REUSED")
    assert count_uploads(factory, owner) == 2
    assert account_reserved(factory, owner) == 2 * MIN_INTENT_RESERVED_BYTES


def test_outra_conta_com_a_mesma_chave_cria_o_proprio_lote(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    _, auth = new_account(factory)
    other, other_auth = new_account(factory)
    first = post_uploads(client, auth, ITEMS, key=KEY)

    second = post_uploads(client, other_auth, ITEMS, key=KEY)

    assert second.status_code == 201, second.text
    assert "Idempotency-Replayed" not in second.headers
    assert set(ids_of(second)).isdisjoint(ids_of(first))
    assert count_uploads(factory, other) == 2


@pytest.mark.parametrize(
    "key",
    [
        pytest.param("k" * 15, id="curta"),
        pytest.param("k" * 65, id="longa"),
        pytest.param("k" * 15 + "!", id="caractere-fora"),
        pytest.param("", id="vazia"),
    ],
)
def test_chave_fora_do_formato_e_400(
    client: TestClient, factory: sessionmaker[Session], key: str
) -> None:
    owner, auth = new_account(factory)

    response = post_uploads(client, auth, ITEMS, key=key)

    assert_problem(response, 400, "IDEMPOTENCY_KEY_INVALID")
    assert count_uploads(factory, owner) == 0


def test_chave_nos_limites_do_formato_e_aceita(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    _, auth = new_account(factory)

    for key in ("a" * 16, "Z9_-" * 16):
        assert post_uploads(client, auth, ITEMS, key=key).status_code == 201


def test_duas_chamadas_simultaneas_criam_um_lote(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    owner, auth = new_account(factory)
    barrier = threading.Barrier(2)

    def post() -> httpx.Response:
        barrier.wait()
        return post_uploads(client, auth, ITEMS, key=KEY)

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: post(), range(2)))

    created = [r for r in responses if r.status_code == 201]
    assert created, [r.text for r in responses]
    for response in responses:
        if response.status_code == 409:
            assert_problem(response, 409, "IDEMPOTENCY_IN_PROGRESS")
        else:
            assert ids_of(response) == ids_of(created[0])
    assert count_uploads(factory, owner) == 2
    assert account_reserved(factory, owner) == 2 * MIN_INTENT_RESERVED_BYTES


def test_pedido_com_a_chave_em_andamento_e_409_com_retry_after(
    app: FastAPI, client: TestClient, factory: sessionmaker[Session]
) -> None:
    owner, auth = new_account(factory)
    engine: Engine = app.state.engine

    # O pool global travado segura o pedido original depois de ele pegar a chave.
    with engine.connect() as blocker, ThreadPoolExecutor(max_workers=1) as pool:
        blocker.execute(
            select(capacity_pool.c.scope).where(capacity_pool.c.scope == "global").with_for_update()
        )
        original = pool.submit(post_uploads, client, auth, ITEMS, KEY)
        wait_for_lock_waiter(engine)

        in_progress = post_uploads(client, auth, ITEMS, key=KEY)

        blocker.rollback()
        first = original.result()

    assert_problem(in_progress, 409, "IDEMPOTENCY_IN_PROGRESS")
    assert in_progress.headers["Retry-After"] == "1"
    assert first.status_code == 201, first.text
    replay = post_uploads(client, auth, ITEMS, key=KEY)
    assert ids_of(replay) == ids_of(first)
    assert count_uploads(factory, owner) == 2


def wait_for_lock_waiter(engine: Engine) -> None:
    """Espera, consultando o banco, até uma sessão ficar bloqueada num lock de linha."""
    deadline = time.monotonic() + WAIT_DEADLINE_SECONDS
    with engine.connect() as conn:
        while time.monotonic() < deadline:
            waiting = conn.execute(
                text("SELECT count(*) FROM pg_stat_activity WHERE wait_event_type = 'Lock'")
            ).scalar_one()
            conn.rollback()
            if waiting:
                return
    raise AssertionError("ninguém ficou esperando o lock")


def test_chave_vencida_cria_lote_novo(client: TestClient, factory: sessionmaker[Session]) -> None:
    owner, auth = new_account(factory)
    first = post_uploads(client, auth, ITEMS, key=KEY)
    with unit_of_work(factory) as session:
        session.execute(
            update(upload)
            .where(upload.c.owner_id == owner)
            .values(created_at=upload.c.created_at - INCOMPLETE_UPLOAD_TTL - timedelta(seconds=1))
        )

    second = post_uploads(client, auth, ITEMS, key=KEY)

    assert second.status_code == 201, second.text
    assert "Idempotency-Replayed" not in second.headers
    assert set(ids_of(second)).isdisjoint(ids_of(first))
    assert count_uploads(factory, owner) == 4


def test_replay_depois_do_envio_devolve_o_estado_atual_sem_reservar(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    owner, auth = new_account(factory)
    first = post_uploads(client, auth, ITEMS, key=KEY)
    sent, deleted = first.json()["items"]
    send_content(client, auth, sent, b"abc")
    assert client.delete(f"{UPLOADS}/{deleted['id']}", headers=auth).status_code == 202
    reserved = account_reserved(factory, owner)

    replay = post_uploads(client, auth, ITEMS, key=KEY)

    assert replay.status_code == 201, replay.text
    assert replay.headers["Idempotency-Replayed"] == "true"
    assert ids_of(replay) == ids_of(first)
    assert [i["state"] for i in replay.json()["items"]] == ["ready", "created"]
    assert account_reserved(factory, owner) == reserved
    assert count_uploads(factory, owner) == 2


def test_sem_chave_cada_pedido_cria_um_lote(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    owner, auth = new_account(factory)

    first = post_uploads(client, auth, ITEMS)
    second = post_uploads(client, auth, ITEMS)

    assert set(ids_of(second)).isdisjoint(ids_of(first))
    assert "Idempotency-Replayed" not in second.headers
    assert count_uploads(factory, owner) == 4
