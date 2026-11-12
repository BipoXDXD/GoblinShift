"""Disco cheio, cota ou erro de E/S no storage responde 503 com `Retry-After`; symlink, 410.

Plano (docs/GoblinShiftPlan.md, 1.6 e 3.2): dependência indisponível é 503, nunca 500. O storage
falso levanta o `OSError` que o disco levanta de verdade; o nome do arquivo no erro leva um canário
para provar que o caminho do storage não chega ao cliente nem ao log. Os testes com banco conferem
o estado do upload e a reserva depois da falha; os offline, só a tradução do erro.
"""

import errno
import logging
import os
import uuid
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.files.tables import artifact, stored_object
from tests.api.files_support import (
    OCTET_STREAM,
    UPLOADS,
    create_upload,
    new_account,
    publish,
    slot_count,
    storage_of,
)
from tests.api.support import assert_problem
from tests.capacity.support import account_reserved, global_reserved

CANARY = "CANARY-caminho-do-storage"
LOGGER = "goblinshift.entrypoints.api.problems"
UNAVAILABLE_ERRNOS = [errno.ENOSPC, errno.EDQUOT, errno.EIO]
OTHER_ERRNOS = [errno.EACCES, errno.EROFS, errno.ENOENT]


def _disk_error(code: int) -> OSError:
    return OSError(code, "erro de disco", f"/srv/storage/{CANARY}/obj.part")


def _failing_storage(code: int) -> Callable[..., tuple[bytes, int]]:
    def create_exclusive(key: str, chunks: Iterable[bytes], max_bytes: int) -> tuple[bytes, int]:
        raise _disk_error(code)

    return create_exclusive


def _assert_no_leak(response_text: str, caplog: pytest.LogCaptureFixture) -> None:
    assert CANARY not in response_text
    assert "Traceback" not in response_text
    assert CANARY not in caplog.text


# --- Tradução do erro (sem banco) --------------------------------------------------------------


@pytest.fixture
def disk_client(offline_client: TestClient) -> TestClient:
    app: FastAPI = offline_client.app  # type: ignore[assignment]

    def disk_error(code: int) -> None:
        raise _disk_error(code)

    app.add_api_route("/_disk/{code}", disk_error)
    return offline_client


@pytest.mark.parametrize("code", UNAVAILABLE_ERRNOS)
def test_oserror_de_disco_indisponivel_responde_503_com_retry_after(
    disk_client: TestClient, caplog: pytest.LogCaptureFixture, code: int
) -> None:
    with caplog.at_level(logging.INFO, logger=LOGGER):
        response = disk_client.get(f"/_disk/{code}")

    body = assert_problem(response, 503, "SERVICE_UNAVAILABLE")
    assert body["instance"] == f"/_disk/{code}"
    assert response.headers["retry-after"] == "60"
    _assert_no_leak(response.text, caplog)
    assert [r.levelno for r in caplog.records if r.name == LOGGER] == [logging.ERROR]


@pytest.mark.parametrize("code", OTHER_ERRNOS)
def test_oserror_de_outro_errno_continua_500(
    disk_client: TestClient, caplog: pytest.LogCaptureFixture, code: int
) -> None:
    with caplog.at_level(logging.INFO, logger=LOGGER):
        response = disk_client.get(f"/_disk/{code}")

    assert_problem(response, 500, "INTERNAL_ERROR")
    assert "retry-after" not in response.headers
    assert CANARY not in response.text
    assert "Traceback" not in response.text


# --- Upload (com banco) ------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.parametrize("code", UNAVAILABLE_ERRNOS)
def test_put_do_conteudo_com_disco_indisponivel_responde_503_e_falha_o_upload(
    client: TestClient,
    app: FastAPI,
    factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    code: int,
) -> None:
    owner, auth = new_account(factory)
    item = create_upload(client, auth, size=3)
    assert account_reserved(factory, owner) == 3
    monkeypatch.setattr(storage_of(app), "create_exclusive", _failing_storage(code))

    with caplog.at_level(logging.INFO, logger=LOGGER):
        response = client.put(
            item["content"]["url"], content=b"abc", headers={**auth, **OCTET_STREAM}
        )

    body = assert_problem(response, 503, "SERVICE_UNAVAILABLE")
    assert body["instance"] == item["content"]["url"]
    assert response.headers["retry-after"] == "60"
    _assert_no_leak(response.text, caplog)
    details: dict[str, Any] = client.get(f"{UPLOADS}/{item['id']}", headers=auth).json()
    assert details["state"] == "failed"
    assert account_reserved(factory, owner) == 0
    assert global_reserved(factory) == 0
    assert slot_count(factory) == 0


@pytest.mark.integration
def test_put_do_conteudo_com_oserror_de_outro_errno_continua_500(
    client: TestClient,
    app: FastAPI,
    factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, auth = new_account(factory)
    item = create_upload(client, auth, size=3)
    monkeypatch.setattr(storage_of(app), "create_exclusive", _failing_storage(errno.EACCES))

    response = client.put(item["content"]["url"], content=b"abc", headers={**auth, **OCTET_STREAM})

    assert_problem(response, 500, "INTERNAL_ERROR")
    assert CANARY not in response.text
    assert client.get(f"{UPLOADS}/{item['id']}", headers=auth).json()["state"] == "failed"


# --- Leitura: download e preview (com banco) ---------------------------------------------------

ARTIFACTS = "/api/v1/artifacts"


def _storage_key_of(factory: sessionmaker[Session], artifact_id: uuid.UUID) -> str:
    with factory() as session:
        key: str = session.execute(
            select(stored_object.c.storage_key)
            .join(artifact, artifact.c.object_id == stored_object.c.id)
            .where(artifact.c.id == artifact_id)
        ).scalar_one()
    return key


@pytest.mark.integration
@pytest.mark.parametrize(
    ("route", "code", "status", "problem_code", "retry_after"),
    [
        pytest.param(
            "download", errno.ENOSPC, 503, "SERVICE_UNAVAILABLE", "60", id="download-ENOSPC"
        ),
        pytest.param("download", errno.EIO, 503, "SERVICE_UNAVAILABLE", "60", id="download-EIO"),
        pytest.param("download", errno.ELOOP, 410, "GONE", None, id="download-ELOOP-symlink"),
        pytest.param(
            "preview", errno.ENOSPC, 503, "SERVICE_UNAVAILABLE", "60", id="preview-ENOSPC"
        ),
        pytest.param("preview", errno.EIO, 503, "SERVICE_UNAVAILABLE", "60", id="preview-EIO"),
        pytest.param("preview", errno.ELOOP, 410, "GONE", None, id="preview-ELOOP-symlink"),
    ],
)
def test_leitura_com_falha_de_es_do_storage_responde_503_ou_410_sem_vazar(
    client: TestClient,
    app: FastAPI,
    factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
    route: str,
    code: int,
    status: int,
    problem_code: str,
    retry_after: str | None,
) -> None:
    # ENOSPC e EIO vêm do `open` do storage; ELOOP é o symlink de verdade, que o `O_NOFOLLOW`
    # vira `ObjectTamperedError`. O preview de uma saída lê o objeto do artefato de preview.
    owner, auth = new_account(factory)
    source = publish(app, owner, b"conteudo", "images/foto.png")
    preview = publish(app, owner, b"jpeg", "previews/foto.jpg", preview_of=source)
    key = _storage_key_of(factory, source if route == "download" else preview)
    storage = storage_of(app)
    if code == errno.ELOOP:
        target = tmp_path / CANARY
        target.write_bytes(b"segredo")
        storage.delete(key)
        (storage.root / key).symlink_to(target)
    else:

        def failing_open(requested: str) -> Any:
            raise _disk_error(code)

        monkeypatch.setattr(storage, "open", failing_open)

    with caplog.at_level(logging.INFO):
        response = client.get(f"{ARTIFACTS}/{source}/{route}", headers=auth)

    assert_problem(response, status, problem_code)
    if retry_after is None:
        assert "retry-after" not in response.headers
    else:
        assert response.headers["retry-after"] == retry_after
        assert response.headers["retry-after"].isdecimal()
    _assert_no_leak(response.text, caplog)
    for leaked in (key, str(storage.root), "erro de disco", os.strerror(code)):
        assert leaked not in response.text
