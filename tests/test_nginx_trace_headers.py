"""O Nginx não repassa à API o contexto de trace que o cliente manda (37 a), sem Docker."""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
NGINX = ROOT / "docker" / "nginx"
SNIPPET = NGINX / "snippets" / "proxy-api.conf"
INCLUDE_SNIPPET = "include /etc/nginx/snippets/proxy-api.conf;"
# Diretivas que encaminham a um backend; só o snippet pode ter uma ativa.
_FORWARDING = re.compile(r"\b(?:proxy|grpc|fastcgi|uwsgi|scgi)_pass\b")


def _active_lines(path: Path) -> list[str]:
    """Linhas sem os comentários (`#` até o fim da linha) nem as vazias."""
    lines = (re.sub(r"(?:^|\s)#.*$", "", line).strip() for line in path.read_text().splitlines())
    return [line for line in lines if line]


def _nginx_configs() -> list[Path]:
    return sorted(p for p in NGINX.rglob("*") if p.is_file() and p.suffix in {".conf", ".template"})


@pytest.mark.parametrize("header", ["traceparent", "tracestate", "baggage"])
def test_proxy_da_api_apaga_o_cabecalho_de_trace_do_cliente(header: str) -> None:
    assert f'proxy_set_header {header} "";' in _active_lines(SNIPPET)


def test_nenhuma_rota_encaminha_a_api_fora_do_snippet() -> None:
    # Uma `location` com `proxy_pass` próprio deixaria passar o `traceparent` do cliente.
    forwarding = {
        f"{path.relative_to(NGINX)}: {line}"
        for path in _nginx_configs()
        if path != SNIPPET
        for line in _active_lines(path)
        if _FORWARDING.search(line)
    }
    assert forwarding == set()
    assert any(_FORWARDING.search(line) for line in _active_lines(SNIPPET))


def test_rotas_da_api_incluem_o_snippet() -> None:
    # Location por prefixo, por `=` e por regex (`~`): todas as da API usam o snippet.
    template = _active_lines(NGINX / "templates" / "default.conf.template")
    including: set[str] = set()
    current: str | None = None
    for line in template:
        if match := re.match(r"location\s+(.+?)\s*\{$", line):
            current = match.group(1)
        elif line == "}":
            # Fecha a location: nenhuma das locations do template tem bloco aninhado.
            current = None
        elif line == INCLUDE_SNIPPET and current is not None:
            including.add(current)
    assert {
        "/api/",
        "/auth/",
        "= /docs",
        "/docs/assets/",
        "= /openapi.json",
        "= /livez",
        "= /readyz",
        "= /api/v1/status",
        r"~ ^/api/v1/uploads/[^/]+/content$",
        r"~ ^/api/v1/artifacts/[^/]+/download$",
    } <= including
