"""`GET /me` com limites e consumo das cotas (1.6, 1.7; plano da fatia 6, Frente D).

Saída por allowlist: o conjunto de chaves é exatamente o publicado, e o consumo é só da conta da
credencial.
"""

import uuid
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.runs.acceptance import RUN_OUTPUT_CEILING_BYTES
from goblinshift.runs.store import db_now
from tests.api.files_support import create_upload, new_account, send_content
from tests.runs.support import insert_finished_run

pytestmark = pytest.mark.integration

ME = "/api/v1/me"
RUNS = "/api/v1/runs"
GIB = 1024**3
MIB = 1024**2
ME_KEYS = {"id", "display_name", "scopes", "via", "limits", "usage", "terms"}
TERMS_KEYS = {"current_version", "accepted_version", "accepted_at", "acceptance_required"}
LIMIT_KEYS = {
    "runs_per_24h",
    "input_bytes_per_24h",
    "storage_bytes",
    "active_runs",
    "file_bytes",
    "batch_files",
    "batch_bytes",
    "concurrent_uploads",
    "concurrent_downloads",
}
USAGE_KEYS = {
    "runs_last_24h",
    "input_bytes_last_24h",
    "storage_reserved_bytes",
    "active_run_id",
    "daily_quota_resets_at",
}


def ready(client: TestClient, auth: dict[str, str], content: bytes) -> str:
    item = create_upload(client, auth, size=len(content))
    send_content(client, auth, item, content)
    upload_id: str = item["id"]
    return upload_id


def test_get_me_chaves_do_json_exatamente(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    _, auth = new_account(factory)

    body = client.get(ME, headers=auth).json()

    assert body.keys() == ME_KEYS
    assert body["limits"].keys() == LIMIT_KEYS
    assert body["usage"].keys() == USAGE_KEYS
    assert body["terms"].keys() == TERMS_KEYS


def test_conta_nova_tem_limites_de_1_7_e_consumo_zero(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    _, auth = new_account(factory)

    body = client.get(ME, headers=auth).json()

    assert body["via"] == "api_key"
    assert body["limits"] == {
        "runs_per_24h": 30,
        "input_bytes_per_24h": 2 * GIB,
        "storage_bytes": 4 * GIB,
        "active_runs": 1,
        "file_bytes": 32 * MIB,
        "batch_files": 100,
        "batch_bytes": 512 * MIB,
        "concurrent_uploads": 2,
        "concurrent_downloads": 2,
    }
    assert body["usage"] == {
        "runs_last_24h": 0,
        "input_bytes_last_24h": 0,
        "storage_reserved_bytes": 0,
        "active_run_id": None,
        "daily_quota_resets_at": None,
    }


def test_consumo_reflete_uploads_execucoes_e_reservas(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    owner, auth = new_account(factory)
    content = b"a,b\n1,2\n"
    upload_id = ready(client, auth, content)
    with factory() as session:
        now = db_now(session)
    oldest = now - timedelta(hours=3)
    insert_finished_run(factory, owner, oldest)
    accepted = client.post(
        RUNS,
        json={
            "recipe_id": "csv-janitor",
            "recipe_version": 1,
            "input_upload_ids": [upload_id],
            "parameters": {},
        },
        headers={**auth, "Idempotency-Key": uuid.uuid4().hex},
    )
    assert accepted.status_code == 202, accepted.text

    usage = client.get(ME, headers=auth).json()["usage"]

    assert usage["runs_last_24h"] == 2
    assert usage["input_bytes_last_24h"] == len(content)
    assert usage["storage_reserved_bytes"] == len(content) + RUN_OUTPUT_CEILING_BYTES
    assert usage["active_run_id"] == accepted.json()["id"]
    resets_at = datetime.fromisoformat(usage["daily_quota_resets_at"])
    assert abs(resets_at - (oldest + timedelta(hours=24))) < timedelta(seconds=1)


def test_consumo_de_outra_conta_nao_aparece(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    other_owner, other_auth = new_account(factory)
    other_upload = ready(client, other_auth, b"segredo")
    with factory() as session:
        insert_finished_run(factory, other_owner, db_now(session) - timedelta(hours=1))
    _, auth = new_account(factory)

    response = client.get(ME, headers=auth)

    assert response.json()["usage"] == {
        "runs_last_24h": 0,
        "input_bytes_last_24h": 0,
        "storage_reserved_bytes": 0,
        "active_run_id": None,
        "daily_quota_resets_at": None,
    }
    assert str(other_owner) not in response.text
    assert other_upload not in response.text
