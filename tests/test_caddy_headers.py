"""Cabeçalhos que os Caddy da frente emitem (#442, 31h e 31k; D8 = b).

- O redirect `www` → domínio também leva o HSTS, com o mesmo valor do Nginx. Sem `preload`.
- Nenhum site anuncia o servidor (`Server: Caddy`).

Testes estáticos. O `caddy validate` da configuração do gateway fica em
tests/test_gateway_client_ip.py (marca `gateway`).
"""

import re
from pathlib import Path

import pytest

from tests.test_edge import _sites

ROOT = Path(__file__).resolve().parent.parent
GATEWAY_SITE = ROOT / "docker" / "gateway" / "sites" / "goblinshift.dev.caddy"
EDGE_SITE = ROOT / "deploy" / "edge" / "conf.d" / "goblinshift.caddy"
NGINX_TEMPLATE = ROOT / "docker" / "nginx" / "templates" / "default.conf.template"


def _hsts_do_nginx() -> str:
    match = re.search(
        r'map \$forwarded_proto \$hsts \{\s*https\s+"([^"]+)";', NGINX_TEMPLATE.read_text()
    )
    assert match
    return match[1]


def test_redirect_do_www_emite_hsts() -> None:
    www = _sites(GATEWAY_SITE)["www.goblinshift.dev"]
    hsts = f'header Strict-Transport-Security "{_hsts_do_nginx()}"'

    assert hsts in www
    assert "preload" not in hsts
    assert www.index(hsts) < next(i for i, d in enumerate(www) if d.startswith("redir "))


@pytest.mark.parametrize("arquivo", [GATEWAY_SITE, EDGE_SITE], ids=lambda p: p.name)
def test_caddy_remove_o_cabecalho_server(arquivo: Path) -> None:
    sites = _sites(arquivo)
    assert sites

    assert {
        site: "header -Server" in diretivas for site, diretivas in sites.items()
    } == dict.fromkeys(sites, True)
