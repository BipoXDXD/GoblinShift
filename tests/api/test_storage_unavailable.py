"""Disco cheio, cota ou erro de E/S ao gravar o upload responde 503 com `Retry-After`.

Plano (docs/GoblinShiftPlan.md, 1.6 e 3.2): dependência indisponível é 503, nunca 500. O storage
falso levanta o `OSError` que o disco levanta de verdade; o nome do arquivo no erro leva um canário
para provar que o caminho do storage não chega ao cliente nem ao log. Os testes com banco conferem
o estado do upload e a reserva depois da falha; os offline, só a tradução do erro.
"""

import errno
import logging
from collections.abc import Callable, Iterable
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from tests.api.files_support import (
    OCTET_STREAM,
    UPLOADS,
    create_upload,
    new_account,
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
    assert response.headers["retry-after"] == "5"
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
    assert response.headers["retry-after"] == "5"
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
