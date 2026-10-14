import re
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
EDGE = ROOT / "deploy" / "edge"


def _sem_comentarios(path: Path) -> str:
    return "\n".join(line.split("#", 1)[0] for line in path.read_text().splitlines())


def _yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text())


def test_borda_e_caddy_por_digest() -> None:
    image = _yaml(EDGE / "compose.yaml")["services"]["proxy"]["image"]
    assert re.fullmatch(r"caddy:2@sha256:[0-9a-f]{64}", image)


def test_borda_publica_so_80_e_443_tcp() -> None:
    assert sorted(_yaml(EDGE / "compose.yaml")["services"]["proxy"]["ports"]) == [
        "443:443",
        "80:80",
    ]


def test_borda_tem_ip_fixo_fora_da_faixa_dinamica() -> None:
    compose = _yaml(EDGE / "compose.yaml")
    assert compose["services"]["proxy"]["networks"]["edge"]["ipv4_address"] == "172.31.0.2"
    ipam = compose["networks"]["edge"]["ipam"]["config"][0]
    assert ipam == {"subnet": "172.31.0.0/24", "ip_range": "172.31.0.128/25"}


def test_borda_nao_confia_em_proxy_nenhum() -> None:
    # Sem trusted_proxies, a borda troca o X-Forwarded-For do cliente pelo IP de quem
    # conectou; os projetos confiam nesse valor só vindo de 172.31.0.2.
    for arquivo in [EDGE / "Caddyfile", *sorted((EDGE / "conf.d").glob("*.caddy"))]:
        assert "trusted_proxies" not in _sem_comentarios(arquivo), arquivo


def test_borda_importa_um_trecho_por_projeto() -> None:
    texto = _sem_comentarios(EDGE / "Caddyfile")
    assert "import /etc/caddy/conf.d/*.caddy" in texto
    assert "protocols h1 h2" in texto


def _sites(path: Path) -> dict[str, list[str]]:
    # Endereço de cada bloco de site de topo -> diretivas de dentro (sem chaves aninhadas).
    sites: dict[str, list[str]] = {}
    for match in re.finditer(r"^(\S[^{\n]*?)\s*\{\n(.*?)^\}", _sem_comentarios(path), re.M | re.S):
        sites[match.group(1)] = [d.strip() for d in match.group(2).splitlines() if d.strip()]
    return sites


@pytest.mark.parametrize("endereco", ["goblinshift.dev", ":80"])
def test_site_do_goblinshift_vai_so_para_o_ip_reservado(endereco: str) -> None:
    sites = _sites(EDGE / "conf.d" / "goblinshift.caddy")
    assert sites[endereco] == ["reverse_proxy 172.31.0.4:80"]


def test_trecho_do_goblinshift_so_tem_o_dominio_e_o_acesso_pelo_ip() -> None:
    # Outro host aqui roubaria o tráfego de outro projeto da borda (ADR 014).
    assert sorted(_sites(EDGE / "conf.d" / "goblinshift.caddy")) == [":80", "goblinshift.dev"]


def test_nginx_na_borda_tem_ip_reservado_e_sem_alias() -> None:
    # IP, e não alias: um segundo container com o mesmo alias dividiria o tráfego em
    # silêncio; com o mesmo IP, o Docker recusa (ADR 014).
    edge = _yaml(ROOT / "compose.prod.yaml")["services"]["nginx"]["networks"]["edge"]
    assert edge == {"ipv4_address": "172.31.0.4"}


def test_nginx_na_borda_nao_tem_net_raw() -> None:
    # Sem NET_RAW, um container comprometido na rede edge não forja ARP para assumir o IP
    # da borda, em quem os projetos confiam (ADR 014).
    assert "NET_RAW" in _yaml(ROOT / "compose.prod.yaml")["services"]["nginx"]["cap_drop"]
