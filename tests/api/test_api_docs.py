"""/docs interativa e contrato em /openapi.json (docs/GoblinShiftPlan.md, 1.6 e 1.8).

O Swagger UI sai da própria origem (sem CDN), com SRI e uma CSP própria e estrita: nenhum script
inline, nem de outra origem. O contrato publica a versão da API, nunca o commit do deploy.
"""

import base64
import hashlib
import re
from collections.abc import Iterator
from importlib.metadata import version
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from goblinshift.entrypoints.api.app import create_app
from goblinshift.platform.settings import Settings
from tests.test_settings_secrets import _production

DOCS_CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)

_SCRIPT_OR_STYLE = re.compile(r"<(?:script|link)\b[^>]*>")
_URL_ATTRIBUTE = re.compile(r'\b(?:src|href)="([^"]+)"')
_INTEGRITY = re.compile(r'\bintegrity="(sha384-[A-Za-z0-9+/=]+)"')


@pytest.fixture
def production_client(tmp_path: Path) -> Iterator[TestClient]:
    settings = _production(tmp_path).model_copy(update={"service_version": "sha-0123abc"})
    with TestClient(create_app(settings)) as client:
        yield client


def _assets(html: str) -> list[tuple[str, str | None]]:
    """(URL, integrity) de cada <script> e <link> do HTML."""
    found = []
    for tag in _SCRIPT_OR_STYLE.findall(html):
        url = _URL_ATTRIBUTE.search(tag)
        assert url, tag
        integrity = _INTEGRITY.search(tag)
        found.append((url.group(1), integrity.group(1) if integrity else None))
    return found


def test_docs_responde_em_producao_com_csp_estrita(production_client: TestClient) -> None:
    response = production_client.get("/docs")

    assert response.status_code == 200
    assert response.headers["content-type"] == "text/html; charset=utf-8"
    assert response.headers.get_list("content-security-policy") == [DOCS_CSP]
    assert 'id="swagger-ui"' in response.text


def test_docs_nao_tem_url_externa_nem_script_inline(production_client: TestClient) -> None:
    html = production_client.get("/docs").text

    assert "http://" not in html
    assert "https://" not in html
    # Todo <script> aponta para um arquivo da própria origem; nenhum traz código no corpo.
    bodies = re.findall(r"<script\b[^>]*>(.*?)</script>", html, re.DOTALL)
    assert bodies
    assert all(body.strip() == "" for body in bodies)
    assert all(url.startswith("/docs/assets/") for url, _ in _assets(html))


def test_cada_asset_da_docs_bate_com_o_sri_e_sai_da_propria_origem(
    production_client: TestClient,
) -> None:
    assets = _assets(production_client.get("/docs").text)

    assert len(assets) == 3
    for url, integrity in assets:
        response = production_client.get(url)
        assert response.status_code == 200, url
        if integrity is not None:
            digest = base64.b64encode(hashlib.sha384(response.content).digest()).decode()
            assert integrity == f"sha384-{digest}", url


def test_bundle_e_css_do_swagger_tem_versao_no_nome_e_cache_imutavel(
    production_client: TestClient,
) -> None:
    for url, integrity in _assets(production_client.get("/docs").text):
        response = production_client.get(url)
        if integrity is None:
            # O inicializador é nosso e muda sem trocar de nome.
            assert response.headers["cache-control"] == "no-cache", url
        else:
            assert re.search(r"-\d+\.\d+\.\d+\.(?:js|css)$", url), url
            assert response.headers["cache-control"] == "public, max-age=31536000, immutable"


def test_inicializador_e_javascript_que_aponta_para_o_contrato(
    production_client: TestClient,
) -> None:
    response = production_client.get("/docs/assets/swagger-init.js")

    assert response.headers["content-type"] == "application/javascript; charset=utf-8"
    assert '"/openapi.json"' in response.text


@pytest.mark.parametrize("name", ["nao-existe.js", "..%2Fapp.py", "swagger-ui-bundle.js"])
def test_asset_fora_da_allowlist_e_404(production_client: TestClient, name: str) -> None:
    response = production_client.get(f"/docs/assets/{name}")

    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"


def test_contrato_publico_traz_a_versao_da_api_e_nao_o_commit(
    production_client: TestClient,
) -> None:
    response = production_client.get("/openapi.json")

    assert response.status_code == 200
    assert response.json()["info"]["version"] == version("goblinshift")
    assert "sha-" not in response.text


def test_fora_de_producao_a_docs_tambem_responde() -> None:
    settings = Settings.model_validate({"environment": "development", "db_port": 1})

    with TestClient(create_app(settings)) as client:
        assert client.get("/docs").status_code == 200
