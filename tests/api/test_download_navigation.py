"""Download por navegação que falha volta à tela da execução (64a, D6 a).

O link "Baixar" da SPA navega até `/api/v1/artifacts/<id>/download`. Quando o download é
recusado, o navegador recebe um 303 para `/execucoes/<uuid>?falha=<code>`, e a tela mostra o
motivo. Clientes que não navegam (curl, clientes da API) continuam com o problem+json.
"""

import uuid
from dataclasses import dataclass
from datetime import timedelta

import httpx2 as httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, update
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.files.states import TransferDirection
from goblinshift.files.tables import artifact
from goblinshift.platform.db import unit_of_work
from tests.api.files_support import new_account, occupy_slots, publish
from tests.api.support import assert_problem

pytestmark = pytest.mark.integration

NAV = {"Accept": "text/html,application/xhtml+xml", "Sec-Fetch-Mode": "navigate"}


@dataclass(frozen=True)
class Published:
    owner: uuid.UUID
    auth: dict[str, str]
    artifact_id: uuid.UUID
    run_id: str


def referer(run_id: str) -> dict[str, str]:
    return {**NAV, "Referer": f"http://testserver/execucoes/{run_id}"}


def download_url(artifact_id: uuid.UUID) -> str:
    return f"/api/v1/artifacts/{artifact_id}/download"


@pytest.fixture
def published(app: FastAPI, factory: sessionmaker[Session]) -> Published:
    owner, auth = new_account(factory)
    artifact_id = publish(app, owner, b"resultado")
    return Published(owner, auth, artifact_id, str(uuid.uuid4()))


def expire(factory: sessionmaker[Session], published: Published) -> None:
    with unit_of_work(factory) as session:
        session.execute(
            update(artifact)
            .where(artifact.c.id == published.artifact_id)
            .values(expires_at=func.now() - timedelta(seconds=1))
        )


def assert_back_to_run(response: httpx.Response, run_id: str, code: str) -> None:
    assert response.status_code == 303, response.text
    assert response.headers["location"] == f"/execucoes/{run_id}?falha={code}"
    assert response.headers["cache-control"] == "no-store"


def test_download_expirado_volta_para_a_execucao_com_gone(
    client: TestClient, factory: sessionmaker[Session], published: Published
) -> None:
    expire(factory, published)

    response = client.get(
        download_url(published.artifact_id),
        headers={**published.auth, **referer(published.run_id)},
        follow_redirects=False,
    )

    assert_back_to_run(response, published.run_id, "GONE")


def test_download_sem_sessao_volta_com_authentication_required(
    client: TestClient, published: Published
) -> None:
    response = client.get(
        download_url(published.artifact_id),
        headers=referer(published.run_id),
        follow_redirects=False,
    )

    assert_back_to_run(response, published.run_id, "AUTHENTICATION_REQUIRED")


def test_download_acima_do_teto_de_transferencias_volta_com_rate_limited(
    client: TestClient, factory: sessionmaker[Session], published: Published
) -> None:
    occupy_slots(factory, published.owner, TransferDirection.DOWNLOAD, 2)

    response = client.get(
        download_url(published.artifact_id),
        headers={**published.auth, **referer(published.run_id)},
        follow_redirects=False,
    )

    assert_back_to_run(response, published.run_id, "RATE_LIMITED")


def test_artefato_inexistente_volta_com_not_found(client: TestClient, published: Published) -> None:
    response = client.get(
        download_url(uuid.uuid4()),
        headers={**published.auth, **referer(published.run_id)},
        follow_redirects=False,
    )

    assert_back_to_run(response, published.run_id, "NOT_FOUND")


def test_navegacao_so_pelo_accept_tambem_volta(
    client: TestClient, factory: sessionmaker[Session], published: Published
) -> None:
    # Navegador sem `Sec-Fetch-*`: o `Accept` com `text/html` basta.
    expire(factory, published)
    headers = {
        **published.auth,
        "Accept": "text/html",
        "Referer": f"http://testserver/execucoes/{published.run_id}",
    }

    response = client.get(
        download_url(published.artifact_id), headers=headers, follow_redirects=False
    )

    assert_back_to_run(response, published.run_id, "GONE")


@pytest.mark.parametrize(
    "headers",
    [{"Accept": "*/*"}, {"Accept": "application/problem+json"}],
    ids=["curl", "cliente-api"],
)
def test_cliente_que_nao_navega_continua_com_problem_json(
    client: TestClient,
    factory: sessionmaker[Session],
    published: Published,
    headers: dict[str, str],
) -> None:
    expire(factory, published)
    page = {"Referer": f"http://testserver/execucoes/{published.run_id}"}

    response = client.get(
        download_url(published.artifact_id),
        headers={**published.auth, **page, **headers},
        follow_redirects=False,
    )

    assert_problem(response, 410, "GONE")


@pytest.mark.parametrize(
    "path",
    [
        "/historico",
        "/execucoes/{id}/../x",
        "/execucoes/{id}x",
        "/execucoes/x{id}",
        "//outro.host/execucoes/{id}",
        "/execucoes/{upper}",
        "",
        None,
    ],
    ids=lambda path: "sem-referer" if path is None else None,
)
def test_referer_fora_da_tela_da_execucao_nao_redireciona(
    client: TestClient, factory: sessionmaker[Session], published: Published, path: str | None
) -> None:
    expire(factory, published)
    headers = {**published.auth, **NAV}
    if path is not None:
        run_page = path.format(id=published.run_id, upper=published.run_id.upper())
        headers["Referer"] = f"http://testserver{run_page}"

    response = client.get(
        download_url(published.artifact_id), headers=headers, follow_redirects=False
    )

    assert_problem(response, 410, "GONE")


def test_navegacao_vinda_de_outro_site_nao_redireciona(
    client: TestClient, factory: sessionmaker[Session], published: Published
) -> None:
    # Um site de terceiro não leva a pessoa a uma tela de execução com um aviso forjado (R17).
    expire(factory, published)
    headers = {**published.auth, **referer(published.run_id), "Sec-Fetch-Site": "cross-site"}

    response = client.get(
        download_url(published.artifact_id), headers=headers, follow_redirects=False
    )

    assert_problem(response, 410, "GONE")


def test_navegacao_da_mesma_origem_declarada_volta(
    client: TestClient, factory: sessionmaker[Session], published: Published
) -> None:
    expire(factory, published)
    headers = {**published.auth, **referer(published.run_id), "Sec-Fetch-Site": "same-origin"}

    response = client.get(
        download_url(published.artifact_id), headers=headers, follow_redirects=False
    )

    assert_back_to_run(response, published.run_id, "GONE")


def test_outra_rota_nao_redireciona_mesmo_navegando(
    client: TestClient, published: Published
) -> None:
    response = client.get(
        f"/api/v1/runs/{uuid.uuid4()}",
        headers={**published.auth, **referer(published.run_id)},
        follow_redirects=False,
    )

    assert_problem(response, 404, "NOT_FOUND")
