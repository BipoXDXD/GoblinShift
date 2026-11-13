"""Portas publicadas pelo Compose de desenvolvimento escutam só no loopback.

A pilha local usa chaves de desenvolvimento e, no `compose.override.yaml`, o Runner local, que
roda a receita sem sandbox. Publicar o Nginx em `0.0.0.0` a exporia, sem querer, a quem está na
mesma rede do notebook (Wi-Fi de café, de evento). Produção não publica porta nenhuma: o tráfego
chega pela borda (`compose.prod.yaml`).
"""

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
DEV_COMPOSE_FILES = ("compose.yaml", "compose.override.yaml", "compose.e2e-gvisor.yaml")
LOOPBACK_HOST = "127.0.0.1"
# IP de abertura da forma curta ("127.0.0.1:${PORTA:-80}:80"): o resto pode ter `:` de variável.
_LEADING_HOST = re.compile(r"(?P<host>\d{1,3}(?:\.\d{1,3}){3}|\[[0-9a-fA-F:]+\]):")


def published_ports(compose_file: str) -> list[tuple[str, str]]:
    """`(serviço, host_ip)` de cada porta publicada; sem IP escrito, o host é `0.0.0.0`."""
    document: dict[str, Any] = yaml.safe_load((ROOT / compose_file).read_text())
    found: list[tuple[str, str]] = []
    for name, service in (document.get("services") or {}).items():
        for port in service.get("ports", []):
            found.append((name, _host_ip(port)))
    return found


def _host_ip(port: str | int | dict[str, Any]) -> str:
    if isinstance(port, dict):
        return str(port.get("host_ip", "0.0.0.0"))  # noqa: S104 - valor padrão do Compose
    # "80:80" e "80" não trazem IP; "127.0.0.1:80:80" traz. IPv6 vem entre colchetes.
    leading = _LEADING_HOST.match(str(port))
    return leading["host"] if leading else "0.0.0.0"  # noqa: S104


@pytest.mark.parametrize("compose_file", DEV_COMPOSE_FILES)
def test_porta_publicada_no_desenvolvimento_so_escuta_no_loopback(compose_file: str) -> None:
    exposed = [name for name, host in published_ports(compose_file) if host != LOOPBACK_HOST]

    assert exposed == []


def test_producao_nao_publica_porta() -> None:
    assert published_ports("compose.prod.yaml") == []


def test_o_nginx_local_continua_publicado_para_o_navegador() -> None:
    names = [name for name, _ in published_ports("compose.override.yaml")]

    assert names == ["nginx"]
