"""Atualização da lista de faixas da Cloudflare do gateway (ADR 041)."""

from ipaddress import IPv4Network, IPv6Network, ip_network
from pathlib import Path

import pytest

from scripts.cloudflare_ips import IPV4_URL, IPV6_URL, LIST_FILE, parse_ranges, render, update

OFFICIAL = {IPV4_URL: "173.245.48.0/20\n103.21.244.0/22\n", IPV6_URL: "2400:cb00::/32\n"}


def test_lista_gravada_tem_uma_linha_por_faixa_na_ordem_recebida(tmp_path: Path) -> None:
    target = tmp_path / "cloudflare-ips.conf"

    update(OFFICIAL.__getitem__, target)

    lines = [line for line in target.read_text().splitlines() if not line.startswith("#")]
    assert lines == [
        "remote_ip 173.245.48.0/20",
        "remote_ip 103.21.244.0/22",
        "remote_ip 2400:cb00::/32",
    ]


@pytest.mark.parametrize("broken_url", [IPV4_URL, IPV6_URL], ids=["falha-na-v4", "falha-na-v6"])
def test_lista_invalida_nao_altera_o_arquivo(tmp_path: Path, broken_url: str) -> None:
    target = tmp_path / "cloudflare-ips.conf"
    target.write_text("original\n")
    responses = {**OFFICIAL, broken_url: "<html>erro</html>\n"}

    with pytest.raises(ValueError, match="does not appear to be"):
        update(responses.__getitem__, target)

    assert target.read_text() == "original\n"


@pytest.mark.parametrize(
    ("text", "version"),
    [
        pytest.param("", 4, id="vazia"),
        pytest.param(" \n\n", 6, id="so-espacos"),
        pytest.param("2400:cb00::/32\n", 4, id="familia-errada"),
        pytest.param("10.0.0.0/20\n", 4, id="faixa-privada"),
        pytest.param("0.0.0.0/0\n", 4, id="a-internet-inteira-v4"),
        pytest.param("::/0\n", 6, id="a-internet-inteira-v6"),
        pytest.param("8.0.0.0/7\n", 4, id="logo-acima-do-limite-v4"),
        pytest.param("2600::/15\n", 6, id="logo-acima-do-limite-v6"),
    ],
)
def test_lista_inesperada_e_recusada(text: str, version: int) -> None:
    with pytest.raises(ValueError, match=r"lista IPv|faixa inesperada"):
        parse_ranges(text, version)


@pytest.mark.parametrize(
    ("line", "version"), [("8.0.0.0/8", 4), ("2600::/16", 6)], ids=["limite-v4", "limite-v6"]
)
def test_faixa_no_limite_de_largura_e_aceita(line: str, version: int) -> None:
    assert parse_ranges(line, version) == [ip_network(line)]


def test_lista_versionada_tem_o_formato_gerado_pelo_script() -> None:
    text = LIST_FILE.read_text()
    ranges: list[IPv4Network | IPv6Network] = [
        ip_network(line.removeprefix("remote_ip "))
        for line in text.splitlines()
        if line.startswith("remote_ip ")
    ]

    assert text == render(ranges)
