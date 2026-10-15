"""Cotas na borda HTTP: 429 `QUOTA_EXCEEDED` e 503 com `Retry-After` (1.6, 1.7; plano 4.1, D).

Recusa da conta → 429 com o tempo até a próxima liberação conhecida (mínimo de 60 s); recusa do
ambiente → 503 `SERVICE_UNAVAILABLE` com 300 s. Nenhuma recusa traz dado de outra conta.
"""

import uuid
from datetime import timedelta

import httpx2 as httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.capacity.tables import capacity_pool
from goblinshift.files.tables import upload
from goblinshift.platform.db import unit_of_work
from goblinshift.runs.quotas import MAX_RUNS_PER_WINDOW
from goblinshift.runs.store import db_now
from tests.api.files_support import UPLOADS, create_upload, new_account, send_content
from tests.api.support import assert_problem
from tests.capacity.support import create_account_pool, set_global_max
from tests.runs.support import insert_finished_run

pytestmark = pytest.mark.integration

RUNS = "/api/v1/runs"
MIN_RETRY_AFTER_S = 60
ENVIRONMENT_RETRY_AFTER_S = 300


def upload_batch(client: TestClient, auth: dict[str, str], size: int) -> httpx.Response:
    return client.post(UPLOADS, json={"items": [{"name": "a.csv", "size": size}]}, headers=auth)


def new_run(client: TestClient, auth: dict[str, str], upload_id: str) -> httpx.Response:
    return client.post(
        RUNS,
        json={
            "recipe_id": "csv-janitor",
            "recipe_version": 1,
            "input_upload_ids": [upload_id],
            "parameters": {},
        },
        headers={**auth, "Idempotency-Key": uuid.uuid4().hex},
    )


def ready(client: TestClient, auth: dict[str, str], content: bytes = b"a,b\n1,2\n") -> str:
    item = create_upload(client, auth, size=len(content))
    send_content(client, auth, item, content)
    upload_id: str = item["id"]
    return upload_id


def test_upload_acima_da_cota_da_conta_429_quota_exceeded(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    owner, auth = new_account(factory)
    create_account_pool(factory, owner, 10)

    response = upload_batch(client, auth, 11)

    assert_problem(response, 429, "QUOTA_EXCEEDED")
    assert int(response.headers["Retry-After"]) == MIN_RETRY_AFTER_S


def test_upload_na_cota_da_conta_e_aceito(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    owner, auth = new_account(factory)
    create_account_pool(factory, owner, 10)

    response = upload_batch(client, auth, 10)

    assert response.status_code == 201


def test_retry_after_da_conta_aponta_a_proxima_expiracao(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    owner, auth = new_account(factory)
    upload_id = ready(client, auth)
    with unit_of_work(factory) as session:
        session.execute(
            update(upload)
            .where(upload.c.id == uuid.UUID(upload_id))
            .values(expires_at=func.now() + timedelta(hours=2))
        )
        session.execute(
            update(capacity_pool)
            .where(capacity_pool.c.owner_id == owner)
            .values(max_amount=capacity_pool.c.reserved)
        )

    response = upload_batch(client, auth, 1)

    assert_problem(response, 429, "QUOTA_EXCEEDED")
    retry_after = int(response.headers["Retry-After"])
    assert 7200 - 60 <= retry_after <= 7200


def test_upload_acima_do_teto_do_ambiente_503_com_retry_after(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    _, auth = new_account(factory)
    set_global_max(factory, 10)

    response = upload_batch(client, auth, 11)

    assert_problem(response, 503, "SERVICE_UNAVAILABLE")
    assert response.headers["Retry-After"] == str(ENVIRONMENT_RETRY_AFTER_S)


def test_31a_execucao_em_24h_429_com_retry_after(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    owner, auth = new_account(factory)
    upload_id = ready(client, auth)
    with factory() as session:
        now = db_now(session)
    for _ in range(MAX_RUNS_PER_WINDOW):
        insert_finished_run(factory, owner, now - timedelta(hours=23))

    response = new_run(client, auth, upload_id)

    assert_problem(response, 429, "QUOTA_EXCEEDED")
    retry_after = int(response.headers["Retry-After"])
    assert 3600 - 60 <= retry_after <= 3600


def test_execucao_sem_espaco_para_o_teto_de_saida_429(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    owner, auth = new_account(factory)
    upload_id = ready(client, auth)
    with unit_of_work(factory) as session:
        session.execute(
            update(capacity_pool)
            .where(capacity_pool.c.owner_id == owner)
            .values(max_amount=capacity_pool.c.reserved)
        )

    response = new_run(client, auth, upload_id)

    assert_problem(response, 429, "QUOTA_EXCEEDED")
    assert int(response.headers["Retry-After"]) >= MIN_RETRY_AFTER_S


def test_execucao_sem_espaco_no_ambiente_503(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    _, auth = new_account(factory)
    upload_id = ready(client, auth)
    with unit_of_work(factory) as session:
        session.execute(
            update(capacity_pool)
            .where(capacity_pool.c.scope == "global")
            .values(max_amount=capacity_pool.c.reserved)
        )

    response = new_run(client, auth, upload_id)

    assert_problem(response, 503, "SERVICE_UNAVAILABLE")
    assert response.headers["Retry-After"] == str(ENVIRONMENT_RETRY_AFTER_S)


def test_recusa_de_cota_nao_traz_dados_de_outra_conta(
    app: FastAPI, client: TestClient, factory: sessionmaker[Session]
) -> None:
    _, other_auth = new_account(factory)
    other_upload = ready(client, other_auth)
    owner, auth = new_account(factory)
    create_account_pool(factory, owner, 10)

    response = upload_batch(client, auth, 11)

    assert response.status_code == 429
    assert other_upload not in response.text
    with factory() as session:
        uploads = session.execute(
            select(func.count()).select_from(upload).where(upload.c.owner_id == owner)
        ).scalar_one()
    assert uploads == 0
