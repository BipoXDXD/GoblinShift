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
from goblinshift.files import uploads as uploads_module
from goblinshift.files.limits import MIN_INTENT_RESERVED_BYTES as MIN
from goblinshift.files.limits import UPLOAD_CREATION_WINDOW
from goblinshift.files.tables import upload
from goblinshift.platform.db import transaction_now, unit_of_work
from goblinshift.runs.quotas import MAX_RUNS_PER_WINDOW
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


def fill_account_pool_releasing_in_two_hours(
    factory: sessionmaker[Session], owner: uuid.UUID, upload_id: str
) -> None:
    """Esgota a cota da conta; a próxima liberação conhecida é o upload, que expira em 2 h."""
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


def test_upload_acima_da_cota_da_conta_429_quota_exceeded(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    owner, auth = new_account(factory)
    create_account_pool(factory, owner, MIN)

    response = upload_batch(client, auth, MIN + 1)

    assert_problem(response, 429, "QUOTA_EXCEEDED")
    assert int(response.headers["Retry-After"]) == MIN_RETRY_AFTER_S


def test_upload_na_cota_da_conta_e_aceito(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    owner, auth = new_account(factory)
    create_account_pool(factory, owner, MIN)

    response = upload_batch(client, auth, 10)

    assert response.status_code == 201


def test_intencao_vazia_sem_espaco_para_o_minimo_429(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    owner, auth = new_account(factory)
    create_account_pool(factory, owner, MIN - 1)

    response = upload_batch(client, auth, 0)

    assert_problem(response, 429, "QUOTA_EXCEEDED")
    assert int(response.headers["Retry-After"]) == MIN_RETRY_AFTER_S


def test_retry_after_com_intencoes_vazias_aponta_o_vencimento_delas(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    owner, auth = new_account(factory)
    create_account_pool(factory, owner, MIN)
    assert upload_batch(client, auth, 0).status_code == 201

    response = upload_batch(client, auth, 0)

    assert_problem(response, 429, "QUOTA_EXCEEDED")
    assert 3600 - 60 <= int(response.headers["Retry-After"]) <= 3600


def test_retry_after_da_conta_aponta_a_proxima_expiracao(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    owner, auth = new_account(factory)
    upload_id = ready(client, auth)
    fill_account_pool_releasing_in_two_hours(factory, owner, upload_id)

    response = upload_batch(client, auth, 1)

    assert_problem(response, 429, "QUOTA_EXCEEDED")
    retry_after = int(response.headers["Retry-After"])
    assert 7200 - 60 <= retry_after <= 7200


def account_with_live_upload_expiring_in(
    client: TestClient, factory: sessionmaker[Session], wait: timedelta
) -> dict[str, str]:
    """Conta com um upload vivo que vence em `wait` e pool de bytes folgado.

    Com o pool folgado, uma recusa só pode vir do teto de uploads vivos.
    """
    owner, auth = new_account(factory)
    create_account_pool(factory, owner, 1024 * MIN)
    upload_id = create_upload(client, auth)["id"]
    with unit_of_work(factory) as session:
        session.execute(
            update(upload)
            .where(upload.c.id == uuid.UUID(upload_id))
            .values(expires_at=func.now() + wait)
        )
    return auth


def test_conta_no_teto_de_uploads_vivos_recebe_429_quota_exceeded_com_retry_after(
    client: TestClient, factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(uploads_module, "MAX_LIVE_UPLOADS_PER_ACCOUNT", 1)
    auth = account_with_live_upload_expiring_in(client, factory, timedelta(hours=2))

    response = upload_batch(client, auth, 0)

    assert_problem(response, 429, "QUOTA_EXCEEDED")
    assert 7200 - 60 <= int(response.headers["Retry-After"]) <= 7200


def test_teto_de_uploads_vivos_com_vencimento_proximo_espera_o_minimo(
    client: TestClient, factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(uploads_module, "MAX_LIVE_UPLOADS_PER_ACCOUNT", 1)
    auth = account_with_live_upload_expiring_in(client, factory, timedelta(seconds=5))

    response = upload_batch(client, auth, 0)

    assert_problem(response, 429, "QUOTA_EXCEEDED")
    assert int(response.headers["Retry-After"]) == MIN_RETRY_AFTER_S


def account_with_deleted_upload_leaving_window_in(
    client: TestClient, factory: sessionmaker[Session], wait: timedelta
) -> dict[str, str]:
    """Conta com um upload excluído que sai da janela de 24 h em `wait` e pool de bytes folgado.

    Excluído, ele não conta no teto de vivos: uma recusa só pode vir do teto de criações.
    """
    owner, auth = new_account(factory)
    create_account_pool(factory, owner, 1024 * MIN)
    upload_id = create_upload(client, auth)["id"]
    assert client.delete(f"{UPLOADS}/{upload_id}", headers=auth).status_code == 202
    with unit_of_work(factory) as session:
        session.execute(
            update(upload)
            .where(upload.c.id == uuid.UUID(upload_id))
            .values(created_at=func.now() - UPLOAD_CREATION_WINDOW + wait)
        )
    return auth


def test_conta_no_teto_de_criacoes_em_24h_recebe_429_quota_exceeded_com_retry_after(
    client: TestClient, factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(uploads_module, "MAX_UPLOADS_CREATED_PER_DAY", 1)
    auth = account_with_deleted_upload_leaving_window_in(client, factory, timedelta(hours=2))

    response = upload_batch(client, auth, 0)

    assert_problem(response, 429, "QUOTA_EXCEEDED")
    assert 7200 - 60 <= int(response.headers["Retry-After"]) <= 7200


def test_teto_de_criacoes_com_saida_proxima_espera_o_minimo(
    client: TestClient, factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(uploads_module, "MAX_UPLOADS_CREATED_PER_DAY", 1)
    auth = account_with_deleted_upload_leaving_window_in(client, factory, timedelta(seconds=5))

    response = upload_batch(client, auth, 0)

    assert_problem(response, 429, "QUOTA_EXCEEDED")
    assert int(response.headers["Retry-After"]) == MIN_RETRY_AFTER_S


def test_upload_acima_do_teto_do_ambiente_503_com_retry_after(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    _, auth = new_account(factory)
    set_global_max(factory, MIN)

    response = upload_batch(client, auth, MIN + 1)

    assert_problem(response, 503, "SERVICE_UNAVAILABLE")
    assert response.headers["Retry-After"] == str(ENVIRONMENT_RETRY_AFTER_S)


def test_31a_execucao_em_24h_429_com_retry_after(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    owner, auth = new_account(factory)
    upload_id = ready(client, auth)
    with factory() as session:
        now = transaction_now(session)
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
    fill_account_pool_releasing_in_two_hours(factory, owner, upload_id)

    response = new_run(client, auth, upload_id)

    assert_problem(response, 429, "QUOTA_EXCEEDED")
    retry_after = int(response.headers["Retry-After"])
    assert 7200 - 60 <= retry_after <= 7200


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
    create_account_pool(factory, owner, MIN)

    response = upload_batch(client, auth, MIN + 1)

    assert response.status_code == 429
    assert other_upload not in response.text
    with factory() as session:
        uploads = session.execute(
            select(func.count()).select_from(upload).where(upload.c.owner_id == owner)
        ).scalar_one()
    assert uploads == 0
