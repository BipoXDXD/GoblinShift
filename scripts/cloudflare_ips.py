"""Regrava a lista de faixas da Cloudflare do gateway com as listas oficiais (ADR 041).

Uso: `uv run python -m scripts.cloudflare_ips`. Depois, revise o diff e publique pelos passos do
ADR 041: o gateway só passa a usar a lista nova depois do `caddy reload`.
"""

from collections.abc import Callable
from ipaddress import IPv4Network, IPv6Network, ip_network
from pathlib import Path
from urllib.request import Request, urlopen

IPV4_URL = "https://www.cloudflare.com/ips-v4"
IPV6_URL = "https://www.cloudflare.com/ips-v6"
SITES = Path(__file__).resolve().parent.parent / "docker" / "gateway" / "sites"
LIST_FILE = SITES / "cloudflare-ips.conf"
FETCH_TIMEOUT_SECONDS = 30
USER_AGENT = "goblinshift-cloudflare-ips"
# Mais largo que isto não é faixa de um provedor: é resposta errada, e confiar nela deixaria
# qualquer um informar o IP do cliente.
WIDEST_PREFIX = {4: 8, 6: 16}
HEADER = """\
# Faixas oficiais da Cloudflare, aceitas como origem do CF-Connecting-IP (ADR 041).
# Fontes: https://www.cloudflare.com/ips-v4 e https://www.cloudflare.com/ips-v6
# Não edite à mão: uv run python -m scripts.cloudflare_ips
"""


def parse_ranges(text: str, version: int) -> list[IPv4Network | IPv6Network]:
    """Faixas de uma lista oficial; recusa a lista inteira se algo não for o esperado."""
    ranges = [ip_network(line.strip()) for line in text.splitlines() if line.strip()]
    if not ranges:
        raise ValueError(f"lista IPv{version} vazia")
    for network in ranges:
        if (
            network.version != version
            or not network.is_global
            or network.prefixlen < WIDEST_PREFIX[version]
        ):
            raise ValueError(f"faixa inesperada na lista IPv{version}: {network}")
    return ranges


def render(ranges: list[IPv4Network | IPv6Network]) -> str:
    return HEADER + "".join(f"remote_ip {network}\n" for network in ranges)


def _fetch(url: str) -> str:
    # O site da Cloudflare responde 403 ao User-Agent padrão do urllib.
    request = Request(url, headers={"User-Agent": USER_AGENT})  # noqa: S310 - URL fixa em https
    with urlopen(request, timeout=FETCH_TIMEOUT_SECONDS) as response:  # noqa: S310
        body: bytes = response.read()
    return body.decode()


def update(fetch: Callable[[str], str], target: Path) -> None:
    """Só grava depois de validar as duas listas: falha na segunda não deixa meia lista."""
    ranges = parse_ranges(fetch(IPV4_URL), 4) + parse_ranges(fetch(IPV6_URL), 6)
    target.write_text(render(ranges))


if __name__ == "__main__":
    update(_fetch, LIST_FILE)
