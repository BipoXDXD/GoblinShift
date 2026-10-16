"""`GET /artifacts/{id}/preview`: só o derivado do serviço, com os cabeçalhos de 3.6 (1.6, 2.2)."""

import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, update
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.files.tables import artifact
from goblinshift.platform.db import unit_of_work
from tests.api.files_support import new_account, open_uses, publish
from tests.api.support import assert_problem

pytestmark = pytest.mark.integration

ARTIFACTS = "/api/v1/artifacts"
JPEG = b"\xff\xd8\xff\xe0" + b"jpeg" * 10
SAMPLE = b"nome\r\nAna\r\n"
PROTECTION = {
    "x-content-type-options": "nosniff",
    "content-security-policy": "default-src 'none'; sandbox",
    "cross-origin-resource-policy": "same-origin",
    "cache-control": "no-store",
}


def with_preview(
    app: FastAPI, owner: uuid.UUID, name: str, content: bytes
) -> tuple[uuid.UUID, uuid.UUID]:
    source = publish(app, owner, b"conteudo", "images/foto.png")
    preview = publish(app, owner, content, name, preview_of=source)
    return source, preview


def test_preview_de_imagem_sai_com_o_tipo_real_e_a_protecao(
    app: FastAPI, client: TestClient, factory: sessionmaker[Session]
) -> None:
    owner, auth = new_account(factory)
    source, _ = with_preview(app, owner, "previews/foto.jpg", JPEG)

    response = client.get(f"{ARTIFACTS}/{source}/preview", headers=auth)

    assert response.status_code == 200
    assert response.content == JPEG
    assert response.headers["content-type"] == "image/jpeg"
    assert response.headers["content-length"] == str(len(JPEG))
    assert response.headers["content-disposition"].startswith('attachment; filename="foto.jpg"')
    assert {key: response.headers[key] for key in PROTECTION} == PROTECTION
    assert open_uses(factory) == 0


def test_amostra_de_tabela_sai_como_bytes_opacos(
    app: FastAPI, client: TestClient, factory: sessionmaker[Session]
) -> None:
    owner, auth = new_account(factory)
    source, _ = with_preview(app, owner, "previews/dados.csv", SAMPLE)

    response = client.get(f"{ARTIFACTS}/{source}/preview", headers=auth)

    assert response.status_code == 200
    assert response.content == SAMPLE
    assert response.headers["content-type"] == "application/octet-stream"
    assert {key: response.headers[key] for key in PROTECTION} == PROTECTION


def test_artefato_sem_preview_responde_404(
    app: FastAPI, client: TestClient, factory: sessionmaker[Session]
) -> None:
    owner, auth = new_account(factory)
    source = publish(app, owner, b"conteudo")

    response = client.get(f"{ARTIFACTS}/{source}/preview", headers=auth)

    assert_problem(response, 404, "NOT_FOUND")


def test_preview_de_outra_conta_responde_404_sem_conteudo(
    app: FastAPI, client: TestClient, factory: sessionmaker[Session]
) -> None:
    owner, _ = new_account(factory)
    source, _ = with_preview(app, owner, "previews/foto.jpg", JPEG)
    _, attacker = new_account(factory)

    response = client.get(f"{ARTIFACTS}/{source}/preview", headers=attacker)

    assert_problem(response, 404, "NOT_FOUND")
    assert b"jpeg" not in response.content
    assert open_uses(factory) == 0


def test_preview_excluido_responde_410(
    app: FastAPI, client: TestClient, factory: sessionmaker[Session]
) -> None:
    owner, auth = new_account(factory)
    source, preview = with_preview(app, owner, "previews/foto.jpg", JPEG)
    with unit_of_work(factory) as session:
        session.execute(
            update(artifact)
            .where(artifact.c.id == preview)
            .values(deleted_at=artifact.c.created_at)
        )

    response = client.get(f"{ARTIFACTS}/{source}/preview", headers=auth)

    assert_problem(response, 410, "GONE")


def test_preview_pedido_pelo_proprio_id_do_preview_responde_404(
    app: FastAPI, client: TestClient, factory: sessionmaker[Session]
) -> None:
    owner, auth = new_account(factory)
    _, preview = with_preview(app, owner, "previews/foto.jpg", JPEG)

    response = client.get(f"{ARTIFACTS}/{preview}/preview", headers=auth)

    assert_problem(response, 404, "NOT_FOUND")


def test_artefato_inexistente_responde_404(
    app: FastAPI, client: TestClient, factory: sessionmaker[Session]
) -> None:
    _, auth = new_account(factory)

    response = client.get(f"{ARTIFACTS}/{uuid.uuid4()}/preview", headers=auth)

    assert_problem(response, 404, "NOT_FOUND")


def test_origem_excluida_responde_410_mesmo_com_o_preview_valido(
    app: FastAPI, client: TestClient, factory: sessionmaker[Session]
) -> None:
    owner, auth = new_account(factory)
    source, _ = with_preview(app, owner, "previews/foto.jpg", JPEG)
    with unit_of_work(factory) as session:
        session.execute(
            update(artifact).where(artifact.c.id == source).values(deleted_at=artifact.c.created_at)
        )

    response = client.get(f"{ARTIFACTS}/{source}/preview", headers=auth)

    assert_problem(response, 410, "GONE")
    assert open_uses(factory) == 0


@pytest.mark.parametrize("expired", ["origem", "preview"])
def test_vencido_antes_de_a_manutencao_marcar_responde_410(
    app: FastAPI, client: TestClient, factory: sessionmaker[Session], expired: str
) -> None:
    # `expires_at` no instante da transação (on point de `expires_at <= now()`) e `deleted_at`
    # ainda nulo: a manutenção não passou, e o vencimento sozinho já tira o conteúdo do ar.
    owner, auth = new_account(factory)
    source, preview = with_preview(app, owner, "previews/foto.jpg", JPEG)
    target = source if expired == "origem" else preview
    with unit_of_work(factory) as session:
        session.execute(
            update(artifact).where(artifact.c.id == target).values(expires_at=func.now())
        )

    response = client.get(f"{ARTIFACTS}/{source}/preview", headers=auth)

    assert_problem(response, 410, "GONE")
    assert b"jpeg" not in response.content
    assert open_uses(factory) == 0
