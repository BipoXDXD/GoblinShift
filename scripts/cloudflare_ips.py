"""Regrava a lista de faixas da Cloudflare do gateway com as listas oficiais (ADR 041).

Uso: `uv run python -m scripts.cloudflare_ips`. Depois, revise o diff e publique pelos passos do
ADR 041: o gateway só passa a usar a lista nova depois do `caddy reload`.

Com `--drift-report CAMINHO`, só compara: não toca na lista e grava o relatório em CAMINHO se ela
divergir das oficiais. É o que o workflow `cloudflare-ips` roda todo dia.
"""

import argparse
from collections.abc import Callable, Sequence
from ipaddress import IPv4Network, IPv6Network, ip_network
from pathlib import Path
from urllib.request import Request, urlopen

IPV4_URL = "https://www.cloudflare.com/ips-v4"
IPV6_URL = "https://www.cloudflare.com/ips-v6"
SITES = Path(__file__).resolve().parent.parent / "docker" / "gateway" / "sites"
LIST_FILE = SITES / "cloudflare-ips.conf"
FETCH_TIMEOUT_SECONDS = 30
USER_AGENT = "goblinshift-cloudflare-ips"
RULE_PREFIX = "remote_ip "
# Mais largo que isto não é faixa de um provedor: é resposta errada, e confiar nela deixaria
# qualquer um informar o IP do cliente.
WIDEST_PREFIX = {4: 8, 6: 16}
HEADER = """\
# Faixas oficiais da Cloudflare, aceitas como origem do CF-Connecting-IP (ADR 041).
# Fontes: https://www.cloudflare.com/ips-v4 e https://www.cloudflare.com/ips-v6
# Não edite à mão: uv run python -m scripts.cloudflare_ips
"""
DRIFT_INTRO = (
    "As listas oficiais da Cloudflare diferem de `docker/gateway/sites/cloudflare-ips.conf`."
)
DRIFT_NEXT_STEP = (
    'A lista não se atualiza sozinha: é revisada e aplicada à mão ("Atualizar a lista", ADR 041).'
)


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
    return HEADER + "".join(f"{RULE_PREFIX}{network}\n" for network in ranges)


def read_ranges(path: Path) -> list[IPv4Network | IPv6Network]:
    """Faixas do arquivo versionado; linha fora do formato gerado por `render` é erro."""
    rules = [
        line for line in path.read_text().splitlines() if line.strip() and not line.startswith("#")
    ]
    for rule in rules:
        if not rule.startswith(RULE_PREFIX):
            raise ValueError(f"linha inesperada em {path.name}: {rule}")
    return [ip_network(rule.removeprefix(RULE_PREFIX)) for rule in rules]


def _fetch(url: str) -> str:
    # O site da Cloudflare responde 403 ao User-Agent padrão do urllib.
    request = Request(url, headers={"User-Agent": USER_AGENT})  # noqa: S310 - URL fixa em https
    with urlopen(request, timeout=FETCH_TIMEOUT_SECONDS) as response:  # noqa: S310
        body: bytes = response.read()
    return body.decode()


def _official_ranges(fetch: Callable[[str], str]) -> list[IPv4Network | IPv6Network]:
    return parse_ranges(fetch(IPV4_URL), 4) + parse_ranges(fetch(IPV6_URL), 6)


def update(fetch: Callable[[str], str], target: Path) -> None:
    """Só grava depois de validar as duas listas: falha na segunda não deixa meia lista."""
    target.write_text(render(_official_ranges(fetch)))


def describe_drift(fetch: Callable[[str], str], target: Path) -> str:
    """Relatório em Markdown do que separa as listas oficiais de `target`; vazio se coincidem.

    Compara conjuntos: a Cloudflare reordenar as linhas não muda o que o gateway aceita.
    """
    official = set(_official_ranges(fetch))
    versioned = set(read_ranges(target))
    sections = [
        _section("Só nas listas oficiais", official - versioned),
        _section("Só no arquivo versionado", versioned - official),
    ]
    differences = "".join(sections)
    if not differences:
        return ""
    return f"{DRIFT_INTRO}\n\n{differences}{DRIFT_NEXT_STEP}\n"


def _section(title: str, ranges: set[IPv4Network | IPv6Network]) -> str:
    if not ranges:
        return ""
    lines = "".join(f"- `{network}`\n" for network in sorted(ranges, key=str))
    return f"**{title}:**\n\n{lines}\n"


def main(
    argv: Sequence[str] | None = None,
    fetch: Callable[[str], str] = _fetch,
    target: Path = LIST_FILE,
) -> None:
    parser = argparse.ArgumentParser(prog="python -m scripts.cloudflare_ips", description=__doc__)
    parser.add_argument("--drift-report", type=Path, metavar="CAMINHO")
    report_path: Path | None = parser.parse_args(argv).drift_report
    if report_path is None:
        update(fetch, target)
        return
    report = describe_drift(fetch, target)
    if report:
        report_path.write_text(report)


if __name__ == "__main__":
    main()
