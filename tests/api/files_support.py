"""Construtores de dados dos testes das rotas de arquivos, pela API ou pelo serviço `files`."""

import errno
import os
import threading
import uuid
from collections.abc import Callable
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Connection, func, select, text
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.files.artifacts import commit_candidate, create_candidate_object, publish_artifact
from goblinshift.files.states import ArtifactRole, TransferDirection
from goblinshift.files.storage import FileStorage
from goblinshift.files.tables import object_use, stored_object, transfer_slot, upload
from goblinshift.files.transfers import acquire_transfer_slot
from goblinshift.platform.db import unit_of_work
from tests.files.support import create_attempt
from tests.identity.support import new_key, new_user

UPLOADS = "/api/v1/uploads"
OCTET_STREAM = {"Content-Type": "application/octet-stream"}


# Falhas do `os.open` do objeto no download e no preview: o disco indisponível vira 503 com
# `Retry-After` de 60 s; o symlink (ELOOP, do `O_NOFOLLOW`) é adulteração e vira 410.
OPEN_FAILURES = [
    pytest.param(errno.ENOSPC, 503, "SERVICE_UNAVAILABLE", id="enospc"),
    pytest.param(errno.EDQUOT, 503, "SERVICE_UNAVAILABLE", id="edquot"),
    pytest.param(errno.EIO, 503, "SERVICE_UNAVAILABLE", id="eio"),
    pytest.param(errno.ELOOP, 410, "GONE", id="eloop"),
]
STORAGE_RETRY_AFTER = "60"


def fail_object_open(monkeypatch: pytest.MonkeyPatch, code: int) -> None:
    """Faz o `os.open` de objetos do storage levantar `OSError(code)`.

    Só a abertura de leitura do objeto que `FileStorage.open` faz (com `O_NOFOLLOW`, relativa ao
    descritor do diretório) é afetada: o resto do processo (driver do banco, diretórios do
    storage, gravação) segue com o `os.open` real.
    """
    real_open = os.open
    skipped = os.O_WRONLY | os.O_DIRECTORY

    def open_with_failure(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        relative = kwargs.get("dir_fd") is not None
        if relative and flags & os.O_NOFOLLOW and not flags & skipped:
            raise OSError(code, os.strerror(code), str(path))
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", open_with_failure)


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
    app: FastAPI,
    owner: uuid.UUID,
    content: bytes,
    file_name: str = "saida.csv",
    *,
    preview_of: uuid.UUID | None = None,
) -> uuid.UUID:
    """Artefato publicado pelo caminho do worker: candidato, gravação, confirmação, publicação.

    Com `preview_of`, publica um preview daquela saída (papel `preview`).
    """
    factory: sessionmaker[Session] = app.state.session_factory
    run_id, attempt_id = create_attempt(factory, owner)
    with unit_of_work(factory) as session:
        candidate = create_candidate_object(session, owner, run_id, attempt_id, 1)
    sha256, size = storage_of(app).create_exclusive(
        candidate.storage_key, [content], max_bytes=len(content)
    )
    with unit_of_work(factory) as session:
        commit_candidate(session, owner, candidate.object_id, sha256, size)
        role = ArtifactRole.OUTPUT if preview_of is None else ArtifactRole.PREVIEW
        return publish_artifact(
            session, owner, candidate.object_id, role, file_name, source_artifact_id=preview_of
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


# Tempo que o pool da API fica esgotado nos testes de encerramento: acima dos 0,5 s que uma
# requisição espera por conexão e abaixo dos 5 s de `db_pool_timeout_s`.
POOL_EXHAUSTED_S = 1.5


class PoolHog:
    """Ocupa todas as conexões do pool da API e as devolve `hold_s` depois, de outra thread."""

    def __init__(self, app: FastAPI, hold_s: float = POOL_EXHAUSTED_S) -> None:
        self._app = app
        self._hold_s = hold_s
        self._timer: threading.Timer | None = None

    def take(self) -> None:
        settings = self._app.state.settings
        size = settings.db_pool_size + settings.db_pool_max_overflow
        connections: list[Connection] = [self._app.state.engine.connect() for _ in range(size)]

        def give_back() -> None:
            for connection in connections:
                connection.close()

        self._timer = threading.Timer(self._hold_s, give_back)
        self._timer.start()

    def join(self) -> None:
        assert self._timer is not None, "o pool não chegou a ser ocupado"
        self._timer.join()


def refusing_pool[**P, R](operation: Callable[P, R], refusals: int = 2) -> Callable[P, R]:
    """`operation` precedida de `refusals` recusas do pool, como sob um pico de requisições."""
    remaining = refusals

    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        nonlocal remaining
        if remaining:
            remaining -= 1
            raise PoolTimeoutError("pool esgotado")
        return operation(*args, **kwargs)

    return wrapper
