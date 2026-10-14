"""HSTS na borda da aplicação: o Nginx manda o header em toda rota, da API e da SPA (3.6; ADR 014).

A API não manda o HSTS (o Nginx da fatia 7 o manda também para a SPA, e o header não pode vir
dobrado). Este teste sobe o Nginx real com os templates do repositório e confere que toda
resposta servida por HTTPS na origem leva o header, e que por HTTP ele não aparece: em
`http://localhost` ele prenderia o navegador ao HTTPS.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.test_nginx_logs import NginxLogs, _docker

pytestmark = pytest.mark.nginx

HSTS = "max-age=63072000; includeSubDomains"
HSTS_PATHS = (
    "/api/v1/me",
    "/api/v1/uploads/x/content",
    "/auth/login/google",
    "/",
    "/assets/nao-existe.js",
)


@pytest.fixture(scope="module")
def stack(tmp_path_factory: pytest.TempPathFactory) -> Iterator[NginxLogs]:
    nginx = NginxLogs(tmp_path_factory.mktemp("nginx-hsts"))
    try:
        nginx.start()
        nginx.request(nginx.other, "/aquecimento")
        yield nginx
    finally:
        nginx.stop()


def _hsts_values(stack: NginxLogs, client: str, path: str, *headers: str) -> list[str]:
    header_args = [arg for header in headers for arg in ("-H", header)]
    dump = _docker(
        "exec", client, "curl", "-sS", "-o", "/dev/null", "-D", "-",
        *header_args, f"http://nginx{path}",
    )  # fmt: skip
    return [
        line.split(":", 1)[1].strip()
        for line in dump.splitlines()
        if line.lower().startswith("strict-transport-security:")
    ]


@pytest.mark.parametrize("path", HSTS_PATHS)
def test_resposta_https_vinda_da_borda_traz_um_hsts(stack: NginxLogs, path: str) -> None:
    assert _hsts_values(stack, stack.edge, path, "X-Forwarded-Proto: https") == [HSTS]


@pytest.mark.parametrize("path", HSTS_PATHS)
def test_resposta_http_nao_traz_hsts(stack: NginxLogs, path: str) -> None:
    assert _hsts_values(stack, stack.edge, path, "X-Forwarded-Proto: http") == []


def test_https_declarado_fora_da_borda_nao_liga_o_hsts(stack: NginxLogs) -> None:
    assert _hsts_values(stack, stack.other, "/api/v1/me", "X-Forwarded-Proto: https") == []


NGINX_DIR = Path(__file__).parent.parent / "docker/nginx"
HSTS_LINE = "add_header Strict-Transport-Security $hsts always;"
HSTS_SNIPPETS = ("spa-headers.conf", "proxy-api.conf")


def _location_blocks(template: str) -> list[str]:
    """Corpo de cada `location` do template (nenhum deles tem bloco aninhado)."""
    blocks: list[str] = []
    for chunk in template.split("location ")[1:]:
        body = chunk.split("{", 1)[1]
        blocks.append(body.split("}", 1)[0])
    return blocks


def test_cada_snippet_de_cabecalhos_declara_um_hsts() -> None:
    """Fitness: o HSTS mora nos snippets, porque `add_header` num `location` anula o do `server`."""
    for name in HSTS_SNIPPETS:
        lines = [line.strip() for line in (NGINX_DIR / "snippets" / name).read_text().splitlines()]
        assert lines.count(HSTS_LINE) == 1, name


def test_toda_location_traz_o_hsts() -> None:
    """Fitness: location nova sem um dos snippets (ou o header próprio) ficaria sem HSTS."""
    template = (NGINX_DIR / "templates/default.conf.template").read_text()
    blocks = _location_blocks(template)
    assert blocks
    for body in blocks:
        includes_snippet = any(f"snippets/{name};" in body for name in HSTS_SNIPPETS)
        assert includes_snippet or HSTS_LINE in body, body


def test_server_nao_declara_hsts_que_nunca_valeria() -> None:
    template = (NGINX_DIR / "templates/default.conf.template").read_text()
    outside_locations = template
    for body in _location_blocks(template):
        outside_locations = outside_locations.replace(body, "")
    assert HSTS_LINE not in outside_locations
