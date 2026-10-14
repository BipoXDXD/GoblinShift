"""Construtores de dados dos testes das rotas de arquivos, pela API ou pelo serviço `files`."""

import uuid
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.files.artifacts import commit_candidate, create_candidate_object, publish_artifact
from goblinshift.files.states import ArtifactRole, TransferDirection
from goblinshift.files.storage import FileStorage
from goblinshift.files.tables import object_use, stored_object, transfer_slot, upload
from goblinshift.files.transfers import acquire_transfer_slot
from goblinshift.platform.db import unit_of_work
from tests.identity.support import new_key, new_user

UPLOADS = "/api/v1/uploads"
OCTET_STREAM = {"Content-Type": "application/octet-stream"}


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def new_account(factory: sessionmaker[Session]) -> tuple[uuid.UUID, dict[str, str]]:
    """Conta com todos os escopos e o header de autenticação da sua chave."""
    user_id = new_user(factory)
    return user_id, bearer(new_key(factory, user_id).token)


def create_upload(
    client: TestClient, auth: dict[str, str], name: str = "dados.csv", size: int = 3
) -> dict[str, Any]:
    response = client.post(UPLOADS, json={"items": [{"name": name, "size": size}]}, headers=auth)
    assert response.status_code == 201, response.text
    item: dict[str, Any] = response.json()["items"][0]
    return item


def send_content(
    client: TestClient, auth: dict[str, str], item: dict[str, Any], content: bytes
) -> dict[str, Any]:
    response = client.put(
        item["content"]["url"], content=content, headers={**auth, **item["content"]["headers"]}
    )
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def storage_of(app: FastAPI) -> FileStorage:
    storage: FileStorage = app.state.storage
    return storage


def publish(
    app: FastAPI, owner: uuid.UUID, content: bytes, file_name: str = "saida.csv"
) -> uuid.UUID:
    """Artefato publicado pelo caminho do worker: candidato, gravação, confirmação, publicação."""
    factory: sessionmaker[Session] = app.state.session_factory
    with unit_of_work(factory) as session:
        candidate = create_candidate_object(session, owner, uuid.uuid4(), uuid.uuid4(), 1)
    sha256, size = storage_of(app).create_exclusive(
        candidate.storage_key, [content], max_bytes=len(content)
    )
    with unit_of_work(factory) as session:
        commit_candidate(session, owner, candidate.object_id, sha256, size)
        return publish_artifact(
            session, owner, candidate.object_id, ArtifactRole.OUTPUT, file_name
        ).id


def occupy_slots(
    factory: sessionmaker[Session], owner: uuid.UUID, direction: TransferDirection, count: int
) -> None:
    """Simula transferências em andamento, inclusive em outra réplica (as vagas ficam no banco)."""
    for _ in range(count):
        with unit_of_work(factory) as session:
            acquire_transfer_slot(session, owner, direction)


def slot_count(factory: sessionmaker[Session]) -> int:
    with factory() as session:
        count: int = session.execute(select(func.count()).select_from(transfer_slot)).scalar_one()
    return count


def open_uses(factory: sessionmaker[Session]) -> int:
    with factory() as session:
        count: int = session.execute(
            select(func.count()).select_from(object_use).where(object_use.c.ended_at.is_(None))
        ).scalar_one()
    return count


def idle_in_transaction(factory: sessionmaker[Session]) -> int:
    """Outras conexões paradas com transação aberta (3.2: nenhuma durante a transferência)."""
    with factory() as session:
        count: int = session.execute(
            text(
                "SELECT count(*) FROM pg_stat_activity"
                " WHERE datname = current_database() AND pid <> pg_backend_pid()"
                " AND state LIKE 'idle in transaction%'"
            )
        ).scalar_one()
    return count


def upload_storage_key(factory: sessionmaker[Session], upload_id: str) -> str:
    with factory() as session:
        key: str = session.execute(
            select(stored_object.c.storage_key)
            .join(upload, upload.c.object_id == stored_object.c.id)
            .where(upload.c.id == uuid.UUID(upload_id))
        ).scalar_one()
    return key


def without_request_identity(body: dict[str, Any]) -> dict[str, Any]:
    """Corpo de erro sem o que muda a cada requisição, para comparar respostas."""
    return {k: v for k, v in body.items() if k not in {"request_id", "instance"}}
