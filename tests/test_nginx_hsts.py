"""HSTS na borda da aplicação: o Nginx manda o header nas rotas da API (3.6; ADR 014).

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
API_PATHS = ("/api/v1/me", "/api/v1/uploads/x/content", "/auth/login/google")


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
        "exec", client, "curl", "-fsS", "-o", "/dev/null", "-D", "-",
        *header_args, f"http://nginx{path}",
    )  # fmt: skip
    return [
        line.split(":", 1)[1].strip()
        for line in dump.splitlines()
        if line.lower().startswith("strict-transport-security:")
    ]


@pytest.mark.parametrize("path", API_PATHS)
def test_resposta_https_vinda_da_borda_traz_um_hsts(stack: NginxLogs, path: str) -> None:
    assert _hsts_values(stack, stack.edge, path, "X-Forwarded-Proto: https") == [HSTS]


@pytest.mark.parametrize("path", API_PATHS)
def test_resposta_http_nao_traz_hsts(stack: NginxLogs, path: str) -> None:
    assert _hsts_values(stack, stack.edge, path, "X-Forwarded-Proto: http") == []


def test_https_declarado_fora_da_borda_nao_liga_o_hsts(stack: NginxLogs) -> None:
    assert _hsts_values(stack, stack.other, "/api/v1/me", "X-Forwarded-Proto: https") == []


def test_template_declara_o_hsts_no_server_inteiro() -> None:
    """Fitness: `add_header` dentro de um `location` anularia o do `server` naquele bloco."""
    template = Path(__file__).parent.parent / "docker/nginx/templates/default.conf.template"
    lines = [line.strip() for line in template.read_text().splitlines()]
    assert lines.count("add_header Strict-Transport-Security $hsts always;") == 1
    assert sum(line.startswith("add_header") for line in lines) == 1
