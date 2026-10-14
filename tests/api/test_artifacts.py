"""Rotas de artefato: metadados e download protegido (1.6, 1.7 e 3.6)."""

import hashlib
import uuid
from datetime import timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.files.retention import expire_artifacts
from goblinshift.files.states import TransferDirection
from goblinshift.files.tables import artifact, stored_object
from goblinshift.platform.db import unit_of_work
from tests.api.files_support import (
    new_account,
    occupy_slots,
    open_uses,
    publish,
    slot_count,
    storage_of,
    without_request_identity,
)
from tests.api.support import assert_problem

pytestmark = pytest.mark.integration

ARTIFACTS = "/api/v1/artifacts"
DOWNLOAD_HEADERS = {
    "content-disposition",
    "content-type",
    "content-length",
    "x-content-type-options",
    "content-security-policy",
    "cross-origin-resource-policy",
    "cache-control",
    "x-request-id",
}


def change_artifact(
    factory: sessionmaker[Session], artifact_id: uuid.UUID, **values: object
) -> None:
    with unit_of_work(factory) as session:
        session.execute(update(artifact).where(artifact.c.id == artifact_id).values(**values))


def storage_key_of(factory: sessionmaker[Session], artifact_id: uuid.UUID) -> str:
    with factory() as session:
        key: str = session.execute(
            select(stored_object.c.storage_key)
            .join(artifact, artifact.c.object_id == stored_object.c.id)
            .where(artifact.c.id == artifact_id)
        ).scalar_one()
    return key


# --- Metadados --------------------------------------------------------------------------------


def test_metadados_trazem_so_os_campos_publicos(
    app: FastAPI, client: TestClient, factory: sessionmaker[Session]
) -> None:
    owner, auth = new_account(factory)
    artifact_id = publish(app, owner, b"resultado")

    response = client.get(f"{ARTIFACTS}/{artifact_id}", headers=auth)

    assert response.status_code == 200
    body = response.json()
    assert body.keys() == {"id", "role", "size", "sha256", "expires_at", "availability"}
    assert body["id"] == str(artifact_id)
    assert (body["role"], body["size"], body["availability"]) == ("output", 9, "available")
    assert body["sha256"] == hashlib.sha256(b"resultado").hexdigest()


def test_metadados_de_artefato_expirado_continuam_visiveis_ao_dono(
    app: FastAPI, client: TestClient, factory: sessionmaker[Session]
) -> None:
    owner, auth = new_account(factory)
    artifact_id = publish(app, owner, b"x")
    change_artifact(factory, artifact_id, expires_at=func.now() - timedelta(seconds=1))

    response = client.get(f"{ARTIFACTS}/{artifact_id}", headers=auth)

    assert response.json()["availability"] == "expired"


def test_artefato_vencido_pela_manutencao_aparece_expirado_e_o_download_responde_410(
    app: FastAPI, client: TestClient, factory: sessionmaker[Session]
) -> None:
    owner, auth = new_account(factory)
    artifact_id = publish(app, owner, b"x")
    change_artifact(factory, artifact_id, expires_at=func.now() - timedelta(seconds=1))
    with factory() as session:
        assert expire_artifacts(session) == 1

    metadata = client.get(f"{ARTIFACTS}/{artifact_id}", headers=auth)
    download = client.get(f"{ARTIFACTS}/{artifact_id}/download", headers=auth)

    assert metadata.json()["availability"] == "expired"
    assert_problem(download, 410, "GONE")


def test_artefato_excluido_pelo_dono_antes_do_prazo_aparece_excluido(
    app: FastAPI, client: TestClient, factory: sessionmaker[Session]
) -> None:
    owner, auth = new_account(factory)
    artifact_id = publish(app, owner, b"x")
    change_artifact(factory, artifact_id, deleted_at=func.now())

    metadata = client.get(f"{ARTIFACTS}/{artifact_id}", headers=auth)

    assert metadata.json()["availability"] == "deleted"


# --- Download ---------------------------------------------------------------------------------


def test_download_entrega_o_conteudo_com_os_cabecalhos_de_protecao(
    app: FastAPI, client: TestClient, factory: sessionmaker[Session]
) -> None:
    owner, auth = new_account(factory)
    content = b"<script>alert(1)</script>"
    artifact_id = publish(app, owner, content)

    response = client.get(f"{ARTIFACTS}/{artifact_id}/download", headers=auth)

    assert response.status_code == 200
    assert response.content == content
    assert set(response.headers.keys()) == DOWNLOAD_HEADERS
    assert response.headers["content-disposition"] == (
        "attachment; filename=\"saida.csv\"; filename*=UTF-8''saida.csv"
    )
    assert response.headers["content-type"] == "application/octet-stream"
    assert response.headers["content-length"] == str(len(content))
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-security-policy"] == "default-src 'none'; sandbox"
    assert response.headers["cross-origin-resource-policy"] == "same-origin"
    assert response.headers["cache-control"] == "no-store"


def test_download_de_saida_num_grupo_anexa_so_o_ultimo_componente_do_caminho(
    app: FastAPI, client: TestClient, factory: sessionmaker[Session]
) -> None:
    owner, auth = new_account(factory)
    artifact_id = publish(app, owner, b"\x89PNG", file_name="images/Foto.png")

    response = client.get(f"{ARTIFACTS}/{artifact_id}/download", headers=auth)

    assert response.status_code == 200
    assert response.headers["content-disposition"] == (
        "attachment; filename=\"Foto.png\"; filename*=UTF-8''Foto.png"
    )


def test_download_encerra_o_uso_e_devolve_a_vaga(
    app: FastAPI, client: TestClient, factory: sessionmaker[Session]
) -> None:
    owner, auth = new_account(factory)
    artifact_id = publish(app, owner, b"resultado")

    client.get(f"{ARTIFACTS}/{artifact_id}/download", headers=auth)

    assert (open_uses(factory), slot_count(factory)) == (0, 0)


@pytest.mark.parametrize(
    "change",
    [
        {"expires_at": func.now() - timedelta(seconds=1)},
        {"deleted_at": func.now()},
    ],
)
def test_download_de_artefato_expirado_ou_excluido_responde_410(
    app: FastAPI, client: TestClient, factory: sessionmaker[Session], change: dict[str, object]
) -> None:
    owner, auth = new_account(factory)
    artifact_id = publish(app, owner, b"x")
    change_artifact(factory, artifact_id, **change)

    response = client.get(f"{ARTIFACTS}/{artifact_id}/download", headers=auth)

    assert_problem(response, 410, "GONE")
    assert (open_uses(factory), slot_count(factory)) == (0, 0)


def test_arquivo_sumido_do_disco_responde_410_e_libera_tudo(
    app: FastAPI, client: TestClient, factory: sessionmaker[Session]
) -> None:
    owner, auth = new_account(factory)
    artifact_id = publish(app, owner, b"x")
    key = storage_key_of(factory, artifact_id)
    storage_of(app).delete(key)

    response = client.get(f"{ARTIFACTS}/{artifact_id}/download", headers=auth)

    assert_problem(response, 410, "GONE")
    assert key not in response.text
    assert (open_uses(factory), slot_count(factory)) == (0, 0)


def test_terceiro_download_simultaneo_responde_429_sem_deixar_uso_aberto(
    app: FastAPI, client: TestClient, factory: sessionmaker[Session]
) -> None:
    owner, auth = new_account(factory)
    artifact_id = publish(app, owner, b"x")
    occupy_slots(factory, owner, TransferDirection.DOWNLOAD, 2)

    response = client.get(f"{ARTIFACTS}/{artifact_id}/download", headers=auth)

    assert_problem(response, 429, "RATE_LIMITED")
    assert int(response.headers["retry-after"]) > 0
    assert open_uses(factory) == 0


# --- Isolamento entre contas ------------------------------------------------------------------


def test_conta_b_recebe_o_mesmo_404_de_artefato_inexistente(
    app: FastAPI, client: TestClient, factory: sessionmaker[Session]
) -> None:
    owner_a, auth_a = new_account(factory)
    _, auth_b = new_account(factory)
    content = b"segredo de A"
    artifact_id = publish(app, owner_a, content)
    key = storage_key_of(factory, artifact_id)

    def attempts(target: uuid.UUID) -> list[dict[str, object]]:
        bodies = []
        for url in (f"{ARTIFACTS}/{target}", f"{ARTIFACTS}/{target}/download"):
            response = client.get(url, headers=auth_b)
            body = assert_problem(response, 404, "NOT_FOUND")
            assert content.decode() not in response.text
            assert key not in response.text
            bodies.append(without_request_identity(body))
        return bodies

    assert attempts(artifact_id) == attempts(uuid.uuid4())
    assert (open_uses(factory), slot_count(factory)) == (0, 0)
    assert client.get(f"{ARTIFACTS}/{artifact_id}/download", headers=auth_a).content == content
