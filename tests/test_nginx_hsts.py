"""HSTS na borda da aplicação: o Nginx manda o header nas respostas servidas por HTTPS (3.6).

A API não manda o HSTS (o Nginx da fatia 7 o manda também para a SPA, e o header não pode vir
dobrado). No Nginx, um `add_header` dentro de um `location` anula todos os herdados do `server`:
quem tem `add_header` próprio (o 429 de `/auth`) repete o HSTS. Os testes estruturais vigiam essa
regra no template; os comportamentais sobem o Nginx real e contam o header em cada tipo de
resposta: um só por HTTPS vindo da borda, nenhum por HTTP (em `http://localhost` ele prenderia o
navegador ao HTTPS) nem com o esquema declarado por quem não é a borda.
"""

import re
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.test_nginx_logs import NginxLogs

pytestmark = pytest.mark.nginx

TEMPLATES = Path(__file__).resolve().parent.parent / "docker" / "nginx" / "templates"
HSTS = "max-age=63072000; includeSubDomains"
HSTS_DIRECTIVE = "add_header Strict-Transport-Security $hsts always;"
# Um caminho de cada tipo de resposta: API, envio de conteúdo, login, e o 404 da API.
PATHS = (
    "/api/v1/me",
    "/api/v1/uploads/x/content",
    "/auth/login/google",
    "/api/v1/runs/00000000-0000-0000-0000-000000000000",
)
# Acima de 10 pedidos com rajada de 10, o 12º seguido já é 429.
REQUESTS_TO_HIT_THE_AUTH_LIMIT = 12
HTTP_NOT_FOUND = 404
HTTP_TOO_MANY_REQUESTS = 429


@pytest.fixture(scope="module")
def stack(tmp_path_factory: pytest.TempPathFactory) -> Iterator[NginxLogs]:
    nginx = NginxLogs(tmp_path_factory.mktemp("nginx-hsts"))
    try:
        nginx.start()
        nginx.request(nginx.other, "/aquecimento")
        yield nginx
    finally:
        nginx.stop()


# --- estrutural -------------------------------------------------------------------------------


def _template_lines() -> list[str]:
    text = (TEMPLATES / "default.conf.template").read_text()
    return [line.strip() for line in text.splitlines() if not line.strip().startswith("#")]


def _split_locations() -> tuple[list[str], dict[str, list[str]]]:
    """Diretivas fora de `location` e as de cada `location` (o template não aninha blocos)."""
    outside: list[str] = []
    blocks: dict[str, list[str]] = {}
    current: str | None = None
    for line in _template_lines():
        if line.startswith("location "):
            current = line.removesuffix("{").strip()
            blocks[current] = []
        elif current is not None and line == "}":
            current = None
        elif current is not None:
            blocks[current].append(line)
        else:
            outside.append(line)
    return outside, blocks


def _location_blocks() -> dict[str, list[str]]:
    return _split_locations()[1]


def test_template_tem_os_locations_esperados() -> None:
    """Guarda do parser: se ele parar de achar os blocos, os outros testes passariam vazios."""
    assert {"location /", "location /auth/", "location @auth_rate_limited"} <= set(
        _location_blocks()
    )


def test_server_inteiro_herda_o_hsts() -> None:
    outside_locations, _ = _split_locations()
    assert outside_locations.count(HSTS_DIRECTIVE) == 1


@pytest.mark.parametrize("location", sorted(_location_blocks()))
def test_location_com_add_header_proprio_repete_o_hsts(location: str) -> None:
    directives = _location_blocks()[location]
    if any(line.startswith("add_header") for line in directives):
        assert directives.count(HSTS_DIRECTIVE) == 1


def test_valor_do_hsts_e_definido_uma_vez_so() -> None:
    # O Nginx aceita `map` duplicado em silêncio, e o último venceria.
    all_templates = "\n".join(path.read_text() for path in TEMPLATES.glob("*.template"))
    assert len(re.findall(r"^map \S+ \$hsts \{", all_templates, flags=re.MULTILINE)) == 1


# --- comportamental ---------------------------------------------------------------------------


def _hsts(stack: NginxLogs, client: str, path: str, *headers: str) -> list[str]:
    return stack.fetch(client, path, *headers).header_values("Strict-Transport-Security")


@pytest.mark.parametrize("path", PATHS)
def test_resposta_https_vinda_da_borda_traz_um_hsts(stack: NginxLogs, path: str) -> None:
    assert _hsts(stack, stack.edge, path, "X-Forwarded-Proto: https") == [HSTS]


@pytest.mark.parametrize("path", PATHS)
def test_resposta_http_nao_traz_hsts(stack: NginxLogs, path: str) -> None:
    assert _hsts(stack, stack.edge, path, "X-Forwarded-Proto: http") == []


def test_404_da_api_por_https_traz_um_hsts(stack: NginxLogs) -> None:
    response = stack.fetch(stack.edge, PATHS[-1], "X-Forwarded-Proto: https")

    assert response.status == HTTP_NOT_FOUND
    assert response.header_values("Strict-Transport-Security") == [HSTS]


def test_429_de_auth_por_https_traz_um_hsts(stack: NginxLogs) -> None:
    responses = stack.fetch_many(
        stack.edge,
        "/auth/login/github",
        REQUESTS_TO_HIT_THE_AUTH_LIMIT,
        "X-Forwarded-Proto: https",
        "X-Forwarded-For: 198.51.100.200",
    )

    assert responses[-1].status == HTTP_TOO_MANY_REQUESTS
    assert responses[-1].header_values("Strict-Transport-Security") == [HSTS]


def test_429_de_auth_por_http_nao_traz_hsts(stack: NginxLogs) -> None:
    responses = stack.fetch_many(
        stack.edge,
        "/auth/login/github",
        REQUESTS_TO_HIT_THE_AUTH_LIMIT,
        "X-Forwarded-Proto: http",
        "X-Forwarded-For: 198.51.100.201",
    )

    assert responses[-1].status == HTTP_TOO_MANY_REQUESTS
    assert responses[-1].header_values("Strict-Transport-Security") == []


def test_https_declarado_fora_da_borda_nao_liga_o_hsts(stack: NginxLogs) -> None:
    assert _hsts(stack, stack.other, "/api/v1/me", "X-Forwarded-Proto: https") == []
