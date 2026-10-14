"""Rotas de upload: intenções em lote, envio do conteúdo, consulta e exclusão (1.6, 1.7 e 3.6)."""

import hashlib
import uuid
from datetime import timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, update
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.files.states import TransferDirection
from goblinshift.files.tables import upload
from goblinshift.identity import Scope
from goblinshift.platform.db import unit_of_work
from tests.api.files_support import (
    OCTET_STREAM,
    UPLOADS,
    bearer,
    create_upload,
    new_account,
    occupy_slots,
    send_content,
    slot_count,
    storage_of,
    upload_storage_key,
    without_request_identity,
)
from tests.api.support import assert_problem
from tests.identity.support import new_key, new_user

pytestmark = pytest.mark.integration

CREATED_KEYS = {"id", "name", "size", "state", "expires_at", "content"}
DETAIL_KEYS = {
    "id",
    "name",
    "size",
    "state",
    "availability",
    "sha256",
    "created_at",
    "ready_at",
    "expires_at",
}


def upload_url(item: dict[str, Any]) -> str:
    return f"{UPLOADS}/{item['id']}"


def expire_upload(factory: sessionmaker[Session], upload_id: str) -> None:
    with unit_of_work(factory) as session:
        session.execute(
            update(upload)
            .where(upload.c.id == uuid.UUID(upload_id))
            .values(expires_at=func.now() - timedelta(seconds=1))
        )


# --- Fluxo completo ---------------------------------------------------------------------------


def test_fluxo_completo_criar_enviar_ler_e_excluir(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    _, auth = new_account(factory)

    created = client.post(
        UPLOADS,
        json={"items": [{"name": "relatório.csv", "size": 6}, {"name": "b.txt", "size": 0}]},
        headers=auth,
    )
    assert created.status_code == 201, created.text
    items = created.json()["items"]
    assert [item.keys() for item in items] == [CREATED_KEYS, CREATED_KEYS]
    first = items[0]
    assert (first["name"], first["size"], first["state"]) == ("relatório.csv", 6, "created")
    assert [item["name"] for item in items] == ["relatório.csv", "b.txt"]
    assert first["content"] == {
        "method": "PUT",
        "url": f"/api/v1/uploads/{first['id']}/content",
        "headers": {"Content-Type": "application/octet-stream"},
    }

    sent = send_content(client, auth, first, b"abcdef")
    assert sent == {
        "id": first["id"],
        "state": "ready",
        "size": 6,
        "sha256": hashlib.sha256(b"abcdef").hexdigest(),
    }

    details = client.get(upload_url(first), headers=auth)
    assert details.status_code == 200
    body = details.json()
    assert body.keys() == DETAIL_KEYS
    assert (body["state"], body["availability"], body["sha256"]) == (
        "ready",
        "available",
        sent["sha256"],
    )

    for _ in range(2):
        deleted = client.delete(upload_url(first), headers=auth)
        assert deleted.status_code == 202
        assert deleted.content == b""

    assert client.get(upload_url(first), headers=auth).json()["availability"] == "deleted"


def test_upload_sem_conteudo_aparece_sem_hash(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    _, auth = new_account(factory)
    item = create_upload(client, auth)

    body = client.get(upload_url(item), headers=auth).json()

    assert (body["state"], body["sha256"], body["ready_at"]) == ("created", None, None)


# --- Intenções --------------------------------------------------------------------------------


def test_lote_com_100_itens_e_aceito_na_ordem(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    _, auth = new_account(factory)
    names = [f"arquivo-{i:03}.png" for i in range(100)]

    response = client.post(
        UPLOADS, json={"items": [{"name": n, "size": 1} for n in names]}, headers=auth
    )

    assert response.status_code == 201
    assert [item["name"] for item in response.json()["items"]] == names


def test_lote_com_101_itens_e_recusado(client: TestClient, factory: sessionmaker[Session]) -> None:
    _, auth = new_account(factory)

    response = client.post(
        UPLOADS, json={"items": [{"name": f"{i}.png", "size": 1} for i in range(101)]}, headers=auth
    )

    body = assert_problem(response, 422, "VALIDATION_FAILED")
    assert [error["field"] for error in body["errors"]] == ["body.items"]


def test_nome_invalido_aponta_o_item_em_errors(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    _, auth = new_account(factory)
    items = [{"name": f"{i}.txt", "size": 1} for i in range(3)] + [{"name": "../x", "size": 1}]

    response = client.post(UPLOADS, json={"items": items}, headers=auth)

    body = assert_problem(response, 422, "VALIDATION_FAILED")
    [error] = body["errors"]
    assert error.keys() == {"field", "code", "message"}
    assert (error["field"], error["code"]) == ("body.items.3.name", "invalid")


def test_tamanho_negativo_aponta_o_campo_size(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    _, auth = new_account(factory)

    response = client.post(UPLOADS, json={"items": [{"name": "a", "size": -1}]}, headers=auth)

    body = assert_problem(response, 422, "VALIDATION_FAILED")
    assert [error["field"] for error in body["errors"]] == ["body.items.0.size"]


@pytest.mark.parametrize("extra", ["owner_id", "state", "id"])
def test_campo_extra_no_item_e_recusado(
    client: TestClient, factory: sessionmaker[Session], extra: str
) -> None:
    user_id, auth = new_account(factory)

    response = client.post(
        UPLOADS,
        json={"items": [{"name": "a", "size": 1, extra: str(user_id)}]},
        headers=auth,
    )

    body = assert_problem(response, 422, "VALIDATION_FAILED")
    assert [(e["field"], e["code"]) for e in body["errors"]] == [
        (f"body.items.0.{extra}", "extra_forbidden")
    ]


def test_criar_uploads_exige_escopo_de_escrita(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    user_id = new_user(factory)
    auth = bearer(new_key(factory, user_id, scopes=frozenset({Scope.FILES_READ})).token)

    response = client.post(UPLOADS, json={"items": [{"name": "a", "size": 1}]}, headers=auth)

    assert_problem(response, 403, "INSUFFICIENT_SCOPE")


def test_contrato_do_lote_aparece_no_openapi(client: TestClient) -> None:
    operation = client.get("/openapi.json").json()["paths"]["/api/v1/uploads"]["post"]

    schema = operation["requestBody"]["content"]["application/json"]["schema"]
    assert schema["properties"]["items"]["items"]["required"] == ["name", "size"]


# --- Envio do conteúdo ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "content_type", [None, "application/json", "text/plain", "multipart/form-data"]
)
def test_envio_sem_octet_stream_e_recusado_sem_mexer_no_upload(
    client: TestClient, factory: sessionmaker[Session], content_type: str | None
) -> None:
    _, auth = new_account(factory)
    item = create_upload(client, auth)
    headers = dict(auth) if content_type is None else {**auth, "Content-Type": content_type}

    response = client.put(item["content"]["url"], content=b"abc", headers=headers)

    assert_problem(response, 415, "UNSUPPORTED_MEDIA_TYPE")
    assert client.get(upload_url(item), headers=auth).json()["state"] == "created"


def test_corpo_maior_que_o_declarado_responde_413_e_falha_o_upload(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    _, auth = new_account(factory)
    item = create_upload(client, auth, size=3)

    response = client.put(item["content"]["url"], content=b"abcd", headers={**auth, **OCTET_STREAM})

    assert_problem(response, 413, "BODY_TOO_LARGE")
    assert client.get(upload_url(item), headers=auth).json()["state"] == "failed"


def test_corpo_menor_que_o_declarado_responde_422_e_falha_o_upload(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    _, auth = new_account(factory)
    item = create_upload(client, auth, size=3)

    response = client.put(item["content"]["url"], content=b"ab", headers={**auth, **OCTET_STREAM})

    body = assert_problem(response, 422, "VALIDATION_FAILED")
    assert [(e["field"], e["code"]) for e in body["errors"]] == [("body", "size_mismatch")]
    assert client.get(upload_url(item), headers=auth).json()["state"] == "failed"


def test_segundo_envio_de_upload_pronto_responde_409(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    _, auth = new_account(factory)
    item = create_upload(client, auth)
    send_content(client, auth, item, b"abc")

    response = client.put(item["content"]["url"], content=b"xyz", headers={**auth, **OCTET_STREAM})

    assert_problem(response, 409, "STATE_CONFLICT")


def test_envio_para_upload_expirado_responde_410(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    _, auth = new_account(factory)
    item = create_upload(client, auth)
    expire_upload(factory, item["id"])

    response = client.put(item["content"]["url"], content=b"abc", headers={**auth, **OCTET_STREAM})

    assert_problem(response, 410, "GONE")


def test_envio_para_upload_excluido_responde_410(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    _, auth = new_account(factory)
    item = create_upload(client, auth)
    client.delete(upload_url(item), headers=auth)

    response = client.put(item["content"]["url"], content=b"abc", headers={**auth, **OCTET_STREAM})

    assert_problem(response, 410, "GONE")


def test_envio_libera_a_vaga_com_sucesso_ou_falha(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    _, auth = new_account(factory)
    ok, too_large = create_upload(client, auth), create_upload(client, auth)

    send_content(client, auth, ok, b"abc")
    client.put(too_large["content"]["url"], content=b"abcd", headers={**auth, **OCTET_STREAM})

    assert slot_count(factory) == 0


# --- Limite de transferências simultâneas -----------------------------------------------------


def test_terceiro_envio_simultaneo_da_conta_responde_429_sem_falhar_o_upload(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    user_id, auth = new_account(factory)
    item = create_upload(client, auth)
    occupy_slots(factory, user_id, TransferDirection.UPLOAD, 2)

    response = client.put(item["content"]["url"], content=b"abc", headers={**auth, **OCTET_STREAM})

    assert_problem(response, 429, "RATE_LIMITED")
    assert int(response.headers["retry-after"]) > 0
    assert client.get(upload_url(item), headers=auth).json()["state"] == "created"
    assert slot_count(factory) == 2


def test_quinto_envio_simultaneo_do_ambiente_responde_429(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    for _ in range(2):
        occupy_slots(factory, new_user(factory), TransferDirection.UPLOAD, 2)
    _, auth = new_account(factory)
    item = create_upload(client, auth)

    response = client.put(item["content"]["url"], content=b"abc", headers={**auth, **OCTET_STREAM})

    assert_problem(response, 429, "RATE_LIMITED")
    assert "retry-after" in response.headers


def test_downloads_em_andamento_nao_bloqueiam_envios(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    user_id, auth = new_account(factory)
    item = create_upload(client, auth)
    occupy_slots(factory, user_id, TransferDirection.DOWNLOAD, 2)

    assert send_content(client, auth, item, b"abc")["state"] == "ready"


# --- Isolamento entre contas ------------------------------------------------------------------


def test_conta_b_recebe_o_mesmo_404_de_upload_inexistente_e_o_de_a_fica_intacto(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    _, auth_a = new_account(factory)
    _, auth_b = new_account(factory)
    item = create_upload(client, auth_a)
    before = client.get(upload_url(item), headers=auth_a).json()

    def attempts(upload_id: str) -> list[dict[str, Any]]:
        url = f"{UPLOADS}/{upload_id}"
        responses = [
            client.get(url, headers=auth_b),
            client.put(f"{url}/content", content=b"abc", headers={**auth_b, **OCTET_STREAM}),
            client.delete(url, headers=auth_b),
        ]
        bodies = []
        for response in responses:
            body = assert_problem(response, 404, "NOT_FOUND")
            assert "dados.csv" not in response.text
            bodies.append(without_request_identity(body))
        return bodies

    assert attempts(item["id"]) == attempts(str(uuid.uuid4()))
    assert client.get(upload_url(item), headers=auth_a).json() == before


def test_id_malformado_na_url_responde_422(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    _, auth = new_account(factory)

    response = client.get(f"{UPLOADS}/nao-e-uuid", headers=auth)

    assert_problem(response, 422, "VALIDATION_FAILED")


def test_erros_nao_expoem_chave_nem_caminho_do_storage(
    app: FastAPI, client: TestClient, factory: sessionmaker[Session]
) -> None:
    _, auth = new_account(factory)
    _, auth_b = new_account(factory)
    item = create_upload(client, auth, size=3)
    key = upload_storage_key(factory, item["id"])
    url = item["content"]["url"]

    responses = [
        client.put(url, content=b"abcd", headers={**auth, **OCTET_STREAM}),
        client.put(url, content=b"abc", headers={**auth, **OCTET_STREAM}),
        client.put(url, content=b"abc", headers={**auth_b, **OCTET_STREAM}),
        client.get(upload_url(item), headers=auth),
    ]

    root = str(storage_of(app).root)
    for response in responses:
        assert key not in response.text
        assert key.split("/")[-1] not in response.text
        assert root not in response.text
        assert "storage_key" not in response.text
        assert "owner_id" not in response.text
