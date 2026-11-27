"""Toda location que encaminha à API tem limite de taxa (#442, 31b; D7).

Sem limite, um cliente em laço com leitura lenta ocupa as 30 conexões do uvicorn
(`--limit-concurrency 30`, docker/app.Dockerfile) e todos passam a receber 503. Teste estático,
sem Docker: o 429 no Nginx real fica em tests/test_nginx_auth_limit.py.
"""

import pytest

from tests.test_nginx_auth_limit import _location_directives
from tests.test_nginx_logs import TEMPLATES

PROXY_API = "include /etc/nginx/snippets/proxy-api.conf"
# location → por que pode encaminhar à API sem limite. Vazia: nenhuma pode hoje.
RATE_LIMIT_EXEMPT: dict[str, str] = {}


def _locations_da_api() -> dict[str, list[str]]:
    locations = _location_directives((TEMPLATES / "default.conf.template").read_text())
    return {nome: corpo for nome, corpo in locations.items() if PROXY_API in corpo}


def test_parser_acha_as_locations_que_encaminham_a_api() -> None:
    """Guarda: se o parser parar de achar os blocos, o teste parametrizado passaria vazio."""
    assert {
        "location /api/",
        "location = /docs",
        "location /docs/assets/",
        "location = /openapi.json",
        "location = /livez",
        "location = /readyz",
        "location /auth/",
    } <= set(_locations_da_api())


@pytest.mark.parametrize("location", sorted(set(_locations_da_api()) - set(RATE_LIMIT_EXEMPT)))
def test_toda_location_que_inclui_proxy_api_tem_limit_req(location: str) -> None:
    corpo = _locations_da_api()[location]

    assert any(linha.startswith("limit_req zone=") for linha in corpo), corpo
    assert any(linha.startswith("error_page 429 = @") for linha in corpo), corpo
