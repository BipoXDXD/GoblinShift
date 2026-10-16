"""scripts/check-spa-edge.sh: a SPA na borda, vista de fora (docs/GoblinShiftPlan.md, 3.6 e 3.8).

Uma borda falsa em `http.server` responde como o Nginx de produção; cada teste estraga um ponto e
confere que o script falha apontando-o.
"""

import os
import subprocess
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check-spa-edge.sh"
SCRIPT_TIMEOUT_S = 60

CSP = (
    "default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; connect-src 'self' blob:; "
    "img-src 'self' blob: data:; style-src 'self'; object-src 'none'; base-uri 'none'; "
    "frame-ancestors 'none'; form-action 'self'"
)
SPA_HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Cross-Origin-Opener-Policy": "same-origin",
}
INDEX = (
    b'<!doctype html><script type="module" src="/assets/index-AbC123.js"></script>'
    b'<link rel="stylesheet" href="/assets/index-DeF456.css">'
)
IMMUTABLE = "public, max-age=31536000, immutable"

Response = tuple[int, dict[str, str], bytes]


def good_edge() -> dict[str, Response]:
    """Rotas da borda certa; o que não está aqui segue a regra de `EdgeHandler.fallback`."""
    page = (200, {"Content-Type": "text/html", "Cache-Control": "no-cache", **SPA_HEADERS}, INDEX)
    return {
        "/": page,
        "/__spa__": page,
        "/assets/index-AbC123.js": (
            200,
            {"Content-Type": "application/javascript", "Cache-Control": IMMUTABLE, **SPA_HEADERS},
            b"export {};",
        ),
        "/assets/index-DeF456.css": (
            200,
            {"Content-Type": "text/css", "Cache-Control": IMMUTABLE, **SPA_HEADERS},
            b"body{}",
        ),
        "/theme-init.js": (
            200,
            {"Content-Type": "application/javascript", "Cache-Control": "no-cache", **SPA_HEADERS},
            b"",
        ),
        "/manifest.webmanifest": (
            200,
            {"Content-Type": "application/manifest+json", "Cache-Control": "no-cache"}
            | SPA_HEADERS,
            b"{}",
        ),
        "/favicon.svg": (
            200,
            {"Content-Type": "image/svg+xml", "Cache-Control": "no-cache", **SPA_HEADERS},
            b"<svg/>",
        ),
        "/.well-known/security.txt": (
            200,
            {"Content-Type": "text/plain; charset=utf-8", "Cache-Control": "no-cache"}
            | SPA_HEADERS,
            b"Contact: x",
        ),
        "/api/v1/rota-que-nao-existe": (
            404,
            {"Content-Type": "application/problem+json"},
            b'{"code": "NOT_FOUND"}',
        ),
        "/openapi.json": (200, {"Content-Type": "application/json"}, b"{}"),
        "/docs": (
            200,
            {"Content-Type": "text/html; charset=utf-8"},
            b'<div id="swagger-ui"></div>',
        ),
        "/livez": (200, {"Content-Type": "application/json"}, b"{}"),
    }


NOT_FOUND: Response = (404, {"Content-Type": "text/html"}, b"not found")


class EdgeHandler(BaseHTTPRequestHandler):
    # Cada teste cria uma subclasse com os seus (fixture `edge`).
    routes: ClassVar[dict[str, Response]] = {}
    methods: ClassVar[list[str]] = []

    def do_GET(self) -> None:
        self.methods.append("GET")
        status, headers, body = self.routes.get(self.path) or self.fallback()
        self.send_response(status)
        for name, value in headers.items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def fallback(self) -> Response:
        # Como o Nginx: /assets/, /.well-known/ e ícones sem fallback; o resto é a SPA.
        if self.path.startswith(("/assets/", "/.well-known/", "/api/")) or self.path.endswith(
            ".png"
        ):
            return NOT_FOUND
        return self.routes.get("/__spa__", NOT_FOUND)

    def log_message(self, format: str, *args: object) -> None:
        return


@pytest.fixture
def edge() -> Iterator[tuple[str, dict[str, Response], list[str]]]:
    routes = good_edge()
    methods: list[str] = []
    handler = type("Handler", (EdgeHandler,), {"routes": routes, "methods": methods})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", routes, methods
    finally:
        server.shutdown()
        server.server_close()


def check(base: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - script do repositório
        ["bash", str(SCRIPT), base],  # noqa: S607 - o bash do PATH, como no terminal
        env={"PATH": os.environ["PATH"]},
        capture_output=True,
        text=True,
        check=False,
        timeout=SCRIPT_TIMEOUT_S,
        cwd=ROOT,
    )


def test_borda_certa_passa_e_so_faz_get(edge: tuple[str, dict[str, Response], list[str]]) -> None:
    base, _, methods = edge

    result = check(base)

    assert result.returncode == 0, result.stderr
    assert "tudo certo" in result.stdout
    assert set(methods) == {"GET"}


def test_sem_borda_falha_dizendo_que_nao_responde() -> None:
    result = check("http://127.0.0.1:9")

    assert result.returncode == 1
    assert "não responde" in result.stderr


def test_asset_sem_cache_imutavel_falha(
    edge: tuple[str, dict[str, Response], list[str]],
) -> None:
    base, routes, _ = edge
    status, headers, body = routes["/assets/index-AbC123.js"]
    routes["/assets/index-AbC123.js"] = (status, headers | {"Cache-Control": "no-cache"}, body)

    result = check(base)

    assert result.returncode == 1
    assert "/assets/index-AbC123.js: Cache-Control sem 'immutable'" in result.stderr


def test_asset_ausente_servido_como_index_falha(
    edge: tuple[str, dict[str, Response], list[str]],
) -> None:
    base, routes, _ = edge
    routes["/assets/nao-existe-check-spa-edge.js"] = routes["/"]

    result = check(base)

    assert result.returncode == 1
    assert "/assets/nao-existe-check-spa-edge.js respondeu 200, esperado 404" in result.stderr


def test_pagina_sem_csp_falha(edge: tuple[str, dict[str, Response], list[str]]) -> None:
    base, routes, _ = edge
    status, headers, body = routes["/"]
    routes["/"] = (
        status,
        {name: value for name, value in headers.items() if name != "Content-Security-Policy"},
        body,
    )

    result = check(base)

    assert result.returncode == 1
    assert "/: Content-Security-Policy sem 'default-src 'self''" in result.stderr


def test_index_com_cache_falha(edge: tuple[str, dict[str, Response], list[str]]) -> None:
    base, routes, _ = edge
    status, headers, body = routes["/"]
    routes["/"] = (status, headers | {"Cache-Control": "max-age=3600"}, body)

    result = check(base)

    assert result.returncode == 1
    assert "/: Cache-Control sem 'no-cache'" in result.stderr


def test_manifesto_com_tipo_errado_falha(
    edge: tuple[str, dict[str, Response], list[str]],
) -> None:
    base, routes, _ = edge
    status, headers, body = routes["/manifest.webmanifest"]
    routes["/manifest.webmanifest"] = (
        status,
        headers | {"Content-Type": "application/octet-stream"},
        body,
    )

    result = check(base)

    assert result.returncode == 1
    assert "/manifest.webmanifest: Content-Type sem 'application/manifest+json'" in result.stderr


def test_erro_da_api_fora_de_problem_json_falha(
    edge: tuple[str, dict[str, Response], list[str]],
) -> None:
    base, routes, _ = edge
    routes["/api/v1/rota-que-nao-existe"] = NOT_FOUND

    result = check(base)

    assert result.returncode == 1
    assert "Content-Type sem 'application/problem+json'" in result.stderr


def test_api_fora_da_borda_cai_no_index_e_falha(
    edge: tuple[str, dict[str, Response], list[str]],
) -> None:
    # Sem as locations da API, o Nginx serviria o index.html com 200 em /livez e /docs.
    base, routes, _ = edge
    del routes["/livez"]
    del routes["/docs"]

    result = check(base)

    assert result.returncode == 1
    assert "/livez: Content-Type sem 'application/json'" in result.stderr
    assert "/docs: o corpo não é a Swagger UI" in result.stderr
    assert "2 verificação(ões) falharam" in result.stderr
