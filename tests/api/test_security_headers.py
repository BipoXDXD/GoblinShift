"""Cabeçalhos de segurança nas respostas da API (docs/GoblinShiftPlan.md, 3.6).

A CSP da SPA é do Nginx (fatia 7): a API manda só `default-src 'none'` nas suas respostas, e o
download mantém a própria CSP com `sandbox`.
"""

import re
from collections.abc import AsyncIterator
from uuid import uuid4

import httpx2 as httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker
from starlette.applications import Starlette
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route
from starlette.types import Receive, Scope, Send

from goblinshift.entrypoints.api.security_headers import SecurityHeadersMiddleware
from tests.api.files_support import new_account, publish
from tests.security.authz_matrix import entries

API_CSP = "default-src 'none'; frame-ancestors 'none'"
HSTS = "max-age=63072000; includeSubDomains"
COMMON = {
    "x-content-type-options": "nosniff",
    "referrer-policy": "strict-origin-when-cross-origin",
    "cross-origin-opener-policy": "same-origin",
    "permissions-policy": "camera=(), microphone=(), geolocation=()",
    "cache-control": "no-store",
}
STREAM_CHUNKS = [b"primeiro ", b"segundo ", b"terceiro"]
DOWNLOAD_HEADERS = {
    "Content-Disposition": "attachment; filename=\"a.txt\"; filename*=UTF-8''a.txt",
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "default-src 'none'; sandbox",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Cache-Control": "no-store",
}


async def _json(_request: object) -> JSONResponse:
    return JSONResponse({"ok": True})


async def _html(_request: object) -> Response:
    return Response("<html></html>", media_type="text/html; charset=utf-8")


async def _private_cache(_request: object) -> JSONResponse:
    return JSONResponse({"ok": True}, headers={"Cache-Control": "private, max-age=60"})


async def _download(_request: object) -> StreamingResponse:
    async def chunks() -> AsyncIterator[bytes]:
        for chunk in STREAM_CHUNKS:
            yield chunk

    return StreamingResponse(
        chunks(), media_type="application/octet-stream", headers=DOWNLOAD_HEADERS
    )


def _client(*, hsts: bool) -> TestClient:
    inner = Starlette(
        routes=[
            Route("/json", _json),
            Route("/html", _html),
            Route("/private-cache", _private_cache),
            Route("/download", _download),
        ]
    )
    return TestClient(SecurityHeadersMiddleware(inner, hsts=hsts))


@pytest.fixture
def bare_client() -> TestClient:
    return _client(hsts=False)


def _header_values(client: TestClient, path: str) -> dict[str, list[str]]:
    response = client.get(path)
    return {name: response.headers.get_list(name) for name in response.headers}


def test_resposta_json_recebe_os_cabecalhos_e_a_csp_da_api(bare_client: TestClient) -> None:
    response = bare_client.get("/json")

    for name, value in {**COMMON, "content-security-policy": API_CSP}.items():
        assert response.headers.get_list(name) == [value], name
    assert response.json() == {"ok": True}


def test_cabecalho_ja_definido_pela_rota_nao_e_sobrescrito_nem_duplicado(
    bare_client: TestClient,
) -> None:
    values = _header_values(bare_client, "/private-cache")

    assert values["cache-control"] == ["private, max-age=60"]


def test_download_por_streaming_mantem_a_propria_csp_e_o_corpo(bare_client: TestClient) -> None:
    response = bare_client.get("/download")

    assert response.content == b"".join(STREAM_CHUNKS)
    for name, value in DOWNLOAD_HEADERS.items():
        assert response.headers.get_list(name) == [value], name
    for name in ("referrer-policy", "cross-origin-opener-policy", "permissions-policy"):
        assert response.headers.get_list(name) == [COMMON[name]], name


def test_html_da_documentacao_nao_recebe_a_csp_da_api(bare_client: TestClient) -> None:
    response = bare_client.get("/html")

    assert "content-security-policy" not in response.headers
    assert response.headers["x-content-type-options"] == "nosniff"


def test_sem_hsts_fora_de_producao(bare_client: TestClient) -> None:
    assert "strict-transport-security" not in bare_client.get("/json").headers


def test_hsts_so_em_producao() -> None:
    response = _client(hsts=True).get("/json")

    assert response.headers.get_list("strict-transport-security") == [HSTS]


@pytest.mark.anyio
async def test_escopo_que_nao_e_http_passa_intacto() -> None:
    seen: list[str] = []

    async def inner(scope: Scope, _receive: Receive, _send: Send) -> None:
        seen.append(scope["type"])

    async def receive() -> dict[str, str]:
        return {"type": "lifespan.startup"}

    async def send(_message: object) -> None:
        raise AssertionError("o middleware não deveria enviar nada")

    middleware = SecurityHeadersMiddleware(inner, hsts=True)
    await middleware({"type": "lifespan"}, receive, send)

    assert seen == ["lifespan"]


# --- Aplicação real ---------------------------------------------------------------------------

_PATH_PARAM = re.compile(r"\{(?P<name>[a-z_]+)\}")
_DOCS = "/docs"


def _assert_common(headers: httpx.Headers, *, csp: str | None = API_CSP) -> None:
    for name, value in COMMON.items():
        assert headers.get_list(name) == [value], name
    if csp is not None:
        assert headers.get_list("content-security-policy") == [csp]
    assert "strict-transport-security" not in headers


@pytest.mark.parametrize(
    "path", [path for method, path, _ in entries() if method == "GET"], ids=str
)
def test_respostas_da_api_tem_cabecalhos_de_3_6(offline_client: TestClient, path: str) -> None:
    concrete = _PATH_PARAM.sub(
        lambda match: "google" if match["name"] == "provider" else str(uuid4()), path
    )

    response = offline_client.get(concrete)

    if path == _DOCS:
        _assert_common(response.headers, csp=None)
        assert "content-security-policy" not in response.headers
    else:
        _assert_common(response.headers)


def test_erro_interno_tambem_recebe_os_cabecalhos(offline_client: TestClient) -> None:
    response = offline_client.get("/api/v1/_test/boom")

    assert response.status_code == 500
    _assert_common(response.headers)


@pytest.mark.integration
def test_download_real_mantem_a_csp_sandbox_e_ganha_so_os_que_faltam(
    app: FastAPI, client: TestClient, factory: sessionmaker[Session]
) -> None:
    owner, auth = new_account(factory)
    content = b"<script>alert(1)</script>" * 10_000
    artifact_id = publish(app, owner, content)

    response = client.get(f"/api/v1/artifacts/{artifact_id}/download", headers=auth)

    assert response.status_code == 200, response.text
    assert response.content == content
    _assert_common(response.headers, csp="default-src 'none'; sandbox")
    assert response.headers.get_list("cross-origin-resource-policy") == ["same-origin"]
