"""Nginx do GoblinShift: serve o build da SPA e encaminha a API (docs/GoblinShiftPlan.md, 2.2,
3.6 e 3.8).

Os testes de contrato leem os arquivos; os de integração constroem `docker/web.Dockerfile` e
sobem a imagem numa rede de teste com a mesma faixa da rede `edge` da VPS (ADR 014): o Nginx no
IP reservado a ele (.4) e uma API falsa com o alias `api`, que devolve em JSON o que recebeu. Sem
Docker, os de integração são pulados. O IP real e o limite de `/auth`
(tests/test_nginx_auth_limit.py) e o HSTS (tests/test_nginx_hsts.py), que dependem de quem é a
borda, têm testes próprios.
"""

import json
import re
import shutil
import subprocess
import textwrap
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = ROOT / "docker" / "web.Dockerfile"
IMAGE = "goblinshift-web:test"
# Mesma base, por digest, da imagem do executor: já está no host de quem roda a suíte.
PYTHON_IMAGE = (
    "python:3.14.8-slim-trixie"
    "@sha256:a2b82f3c48559aa0a8446d9af49826b6e2b2016f4cd2afabfe6013ec53729170"
)
EDGE_SUBNET = "172.31.0.0/24"
# Faixa dinâmica fora dos IPs fixos, como na rede edge (deploy/edge/compose.yaml).
EDGE_DYNAMIC_RANGE = "172.31.0.128/25"
NGINX_IP = "172.31.0.4"
BUILD_TIMEOUT_SECONDS = 900
RUN_TIMEOUT_SECONDS = 60
READY_TIMEOUT_SECONDS = 30
READY_POLL_SECONDS = 0.2

SPA_CSP = (
    "default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; connect-src 'self' blob:; "
    "img-src 'self' blob: data:; "
    "style-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; "
    "form-action 'self'"
)
IMMUTABLE = "public, max-age=31536000, immutable"


def _yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text())


# Contrato nos arquivos ---------------------------------------------------------------------


def test_estagio_node_do_build_e_fixado_por_digest() -> None:
    texto = DOCKERFILE.read_text()
    assert re.search(
        r"^FROM node:24\.\d+\.\d+-trixie-slim@sha256:[0-9a-f]{64} AS build$", texto, re.MULTILINE
    )


def test_nginx_da_imagem_e_fixado_por_digest() -> None:
    texto = DOCKERFILE.read_text()
    assert re.search(r"^FROM nginx:1\.\d+\.\d+@sha256:[0-9a-f]{64}$", texto, re.MULTILINE)


def test_estagio_final_liga_o_pacote_ao_repositorio() -> None:
    # Sem o rótulo de origem, o pacote nasce no GHCR solto do repositório e sem o acesso do
    # Actions; ele só vale no estágio final, que é o que vira a imagem publicada.
    estagio_final = DOCKERFILE.read_text().rsplit("\nFROM ", 1)[1]
    assert re.search(
        r"^LABEL org\.opencontainers\.image\.source=https://github\.com/BipoXDXD/GoblinShift$",
        estagio_final,
        re.MULTILINE,
    )


def test_compose_constroi_o_nginx_com_a_spa_e_sem_montar_o_template() -> None:
    nginx = _yaml(ROOT / "compose.yaml")["services"]["nginx"]
    assert nginx["image"] == "ghcr.io/bipoxdxd/goblinshift-web:${GS_IMAGE_TAG:-dev}"
    assert nginx["build"] == {"context": ".", "dockerfile": "docker/web.Dockerfile"}
    # A configuração vem da imagem, junto do build que ela serve; montada do checkout, as duas
    # poderiam divergir.
    assert "volumes" not in nginx


def test_ci_publica_a_imagem_web_antes_do_deploy() -> None:
    jobs = _yaml(ROOT / ".github" / "workflows" / "ci.yml")["jobs"]
    publish = next(
        step for step in jobs["web-image"]["steps"] if step.get("uses", "").startswith("docker/b")
    )
    assert publish["with"]["file"] == "docker/web.Dockerfile"
    assert "ghcr.io/bipoxdxd/goblinshift-web:${{ steps.vars.outputs.tag }}" in publish["with"][
        "tags"
    ].split("\n")
    assert "web-image" in jobs["deploy"]["needs"]


# Integração com a imagem -------------------------------------------------------------------

FAKE_API = textwrap.dedent(
    """
    import json
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Echo(BaseHTTPRequestHandler):
        def _answer(self):
            length = int(self.headers.get("Content-Length") or 0)
            self.rfile.read(length)
            body = json.dumps({
                "served_by": "api",
                "method": self.command,
                "path": self.path,
                "x_forwarded_for": self.headers.get("X-Forwarded-For"),
                "x_forwarded_proto": self.headers.get("X-Forwarded-Proto"),
                "body_bytes": length,
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_GET = do_POST = do_PUT = do_DELETE = _answer

        def log_message(self, *args):
            pass

    ThreadingHTTPServer(("0.0.0.0", 8000), Echo).serve_forever()
    """
)


def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    probe = subprocess.run(
        ["docker", "info", "--format", "{{.ServerVersion}}"],  # noqa: S607
        capture_output=True,
        check=False,
        timeout=RUN_TIMEOUT_SECONDS,
    )
    return probe.returncode == 0


def docker(*args: str, timeout: int = RUN_TIMEOUT_SECONDS) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - argumentos montados pelo teste, sem shell
        ["docker", *args],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )


def docker_ok(*args: str, timeout: int = RUN_TIMEOUT_SECONDS) -> str:
    result = docker(*args, timeout=timeout)
    assert result.returncode == 0, result.stderr
    return result.stdout


@dataclass(frozen=True)
class Response:
    status: int
    headers: dict[str, str]
    body: bytes

    def json(self) -> Any:
        return json.loads(self.body)


@dataclass(frozen=True)
class Stack:
    base_url: str

    def get(self, path: str, *, method: str = "GET", data: bytes | None = None) -> Response:
        request = urllib.request.Request(self.base_url + path, data=data, method=method)  # noqa: S310
        try:
            with urllib.request.urlopen(request, timeout=RUN_TIMEOUT_SECONDS) as response:  # noqa: S310
                return Response(response.status, _lower(response.headers), response.read())
        except urllib.error.HTTPError as error:
            with error:
                return Response(error.code, _lower(error.headers), error.read())


def _lower(headers: Any) -> dict[str, str]:
    return {name.lower(): value for name, value in headers.items()}


pytestmark_integration = [
    pytest.mark.nginx_image,
    pytest.mark.skipif(not _docker_available(), reason="precisa de Docker"),
]


@pytest.fixture(scope="module")
def stack() -> Iterator[Stack]:
    docker_ok(
        "build", "--quiet", "--file", str(DOCKERFILE), "--tag", IMAGE, str(ROOT),
        timeout=BUILD_TIMEOUT_SECONDS,
    )  # fmt: skip
    suffix = uuid.uuid4().hex[:8]
    network = f"gs-nginx-test-{suffix}"
    names = [f"gs-nginx-test-{role}-{suffix}" for role in ("api", "nginx")]
    api, nginx = names
    docker_ok("network", "create", "--subnet", EDGE_SUBNET, "--ip-range", EDGE_DYNAMIC_RANGE,
              network)  # fmt: skip
    try:
        common = ["run", "--detach", "--name"]
        docker_ok(*common, api, "--network", network, "--network-alias", "api",
                  PYTHON_IMAGE, "python", "-c", FAKE_API)  # fmt: skip
        docker_ok(*common, nginx, "--network", network, "--ip", NGINX_IP,
                  "--publish", "127.0.0.1::80", IMAGE)  # fmt: skip
        port = docker_ok("port", nginx, "80/tcp").splitlines()[0].rsplit(":", 1)[1]
        current = Stack(f"http://127.0.0.1:{port}")
        _wait_until_ready(current)
        yield current
    finally:
        docker("rm", "--force", *names)
        docker("network", "rm", network)


def _wait_until_ready(stack: Stack) -> None:
    deadline = time.monotonic() + READY_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        try:
            if stack.get("/readyz").status == 200:
                return
        except OSError:
            pass
        time.sleep(READY_POLL_SECONDS)
    pytest.fail("o Nginx e a API falsa não ficaram prontos")


def _first_asset(stack: Stack, extension: str) -> str:
    index = stack.get("/").body.decode()
    match = re.search(rf'"(/assets/[^"]+\.{extension})"', index)
    assert match, index
    return match.group(1)


def _assert_spa_security_headers(response: Response) -> None:
    assert response.headers["content-security-policy"] == SPA_CSP
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert response.headers["cross-origin-opener-policy"] == "same-origin"
    assert response.headers["permissions-policy"] == "camera=(), microphone=(), geolocation=()"


class TestSpa:
    pytestmark = pytestmark_integration

    def test_raiz_serve_o_index_sem_cache_e_com_os_cabecalhos_de_seguranca(
        self, stack: Stack
    ) -> None:
        response = stack.get("/")

        assert response.status == 200
        assert response.headers["content-type"].startswith("text/html")
        assert b'<div id="root"></div>' in response.body
        assert response.headers["cache-control"] == "no-cache"
        _assert_spa_security_headers(response)

    @pytest.mark.parametrize("path", ["/qualquer-rota", "/runs/0192f1c2-aaaa/resultado", "/x?y=1"])
    def test_rota_da_spa_cai_no_index(self, stack: Stack, path: str) -> None:
        response = stack.get(path)

        assert response.status == 200
        assert response.body == stack.get("/").body
        assert response.headers["cache-control"] == "no-cache"
        _assert_spa_security_headers(response)

    @pytest.mark.parametrize("extension", ["js", "css"])
    def test_asset_com_hash_tem_cache_imutavel(self, stack: Stack, extension: str) -> None:
        response = stack.get(_first_asset(stack, extension))

        assert response.status == 200
        assert response.headers["cache-control"] == IMMUTABLE
        assert b"<!doctype html>" not in response.body.lower()
        _assert_spa_security_headers(response)

    def test_asset_inexistente_e_404_e_nao_o_index(self, stack: Stack) -> None:
        response = stack.get("/assets/nao-existe-0000.js")

        assert response.status == 404
        assert b'<div id="root">' not in response.body
        assert response.headers["x-content-type-options"] == "nosniff"

    def test_servidor_nao_anuncia_a_versao(self, stack: Stack) -> None:
        assert stack.get("/").headers["server"] == "nginx"

    def test_post_na_spa_nao_chega_a_api(self, stack: Stack) -> None:
        response = stack.get("/qualquer-rota", method="POST", data=b"{}")

        assert response.status == 405


class TestApi:
    pytestmark = pytestmark_integration

    @pytest.mark.parametrize(
        "path",
        [
            "/api/v1/runs?limit=5",
            "/api/v1/rota-que-nao-existe",
            "/auth/login/google",
            "/docs",
            "/openapi.json",
            "/livez",
            "/readyz",
        ],
    )
    def test_rotas_do_backend_vao_para_a_api(self, stack: Stack, path: str) -> None:
        response = stack.get(path)

        assert response.status == 200
        assert response.json()["served_by"] == "api"
        assert response.json()["path"] == path

    def test_resposta_da_api_nao_recebe_a_csp_da_spa(self, stack: Stack) -> None:
        # A API define a própria CSP (downloads com sandbox); a /docs carrega o Swagger UI.
        response = stack.get("/api/v1/me")

        assert "content-security-policy" not in response.headers

    def test_upload_de_conteudo_aceita_corpo_grande(self, stack: Stack) -> None:
        body = b"x" * 1_000_000
        response = stack.get("/api/v1/uploads/abc/content", method="PUT", data=body)

        assert response.status == 200
        assert response.json()["body_bytes"] == len(body)

    def test_comando_json_acima_do_teto_e_413(self, stack: Stack) -> None:
        response = stack.get("/api/v1/runs", method="POST", data=b"x" * 100_000)

        assert response.status == 413
