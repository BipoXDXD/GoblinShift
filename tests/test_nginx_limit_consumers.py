"""Quem consome `/api/` e `/readyz` de um IP só cabe no `limit_req` do Nginx.

O Nginx limita `/api/` e `/readyz` por cliente (docker/nginx/templates/default.conf.template). Os
clientes legítimos que mandam muitos pedidos de um único IP são os cenários do k6
(deploy/load), que saem todos do container do k6, e a prova de troca sem queda
(scripts/check-zero-downtime.sh). Se um limite ou um ritmo mudar, estes testes apontam o lado a
ajustar, em vez de o k6 ou a prova falharem com 429 na próxima rodada.
"""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NGINX_TEMPLATE = ROOT / "docker" / "nginx" / "templates" / "default.conf.template"
SCENARIOS = ROOT / "deploy" / "load" / "scenarios"
ZERO_DOWNTIME = ROOT / "scripts" / "check-zero-downtime.sh"
COOLIFY_APP = ROOT / "deploy" / "coolify" / "app.json"
SECONDS_PER_MINUTE = 60


def _zone_rate_per_second(zone: str) -> float:
    template = NGINX_TEMPLATE.read_text(encoding="utf-8")
    match = re.search(rf"limit_req_zone \S+ zone={zone}:\S+ rate=(\d+)r/s;", template)
    assert match is not None, f"zona {zone} sem taxa por segundo no template"
    return float(match.group(1))


def _js_number(source: str, name: str) -> float:
    # Aceita `const X = 5`, `Math.min(..., 5)` e `Number(__ENV.Y || 5)`: o número é o padrão.
    prefix = r"(?:Math\.min\([^,]+, )?(?:Number\([^|]+\|\| )?"
    match = re.search(rf"\bconst {name} = {prefix}([\d.]+)", source)
    assert match is not None, f"constante {name} não encontrada"
    return float(match.group(1))


def test_limite_por_conta_espaca_os_pedidos_abaixo_do_limite_da_api() -> None:
    """Os três VUs (uma conta por classe) saem do mesmo IP e rodam ao mesmo tempo."""
    source = (SCENARIOS / "limite-por-conta.js").read_text(encoding="utf-8")
    vus = len(re.findall(r"^\s+account: \d+,$", source, flags=re.MULTILINE))
    interval = _js_number(source, "REQUEST_INTERVAL_SECONDS")

    assert vus == 3
    assert vus / interval <= _zone_rate_per_second("api")


def test_limite_por_conta_ainda_cabe_na_janela_de_um_minuto() -> None:
    """O cenário só prova o limite da conta se a maior rajada terminar no mesmo minuto."""
    source = (SCENARIOS / "limite-por-conta.js").read_text(encoding="utf-8")
    largest = max(int(limit) for limit in re.findall(r"^\s+limit: (\d+),$", source, re.MULTILINE))
    interval = _js_number(source, "REQUEST_INTERVAL_SECONDS")
    latest_start = _js_number(source, "LATEST_START_SECOND")

    assert latest_start + (largest + 1) * interval < SECONDS_PER_MINUTE


def test_jornada_consulta_abaixo_do_limite_da_api() -> None:
    """Cada VU consulta `GET /runs/{id}` a cada intervalo; os VUs saem do mesmo IP.

    As consultas ficam com no máximo metade da taxa: o resto é dos envios, das novas execuções e
    dos downloads. A rodada de 2026-10-16 (deploy/load/README.md) mediu 10,7 req/s no total.
    """
    source = (SCENARIOS / "jornada.js").read_text(encoding="utf-8")
    vus = _js_number(source, "VUS")
    interval = _js_number(source, "POLL_INTERVAL_SECONDS")

    assert vus / interval <= _zone_rate_per_second("api") / 2


def test_prova_de_troca_sem_queda_consulta_abaixo_do_limite_do_readyz() -> None:
    script = ZERO_DOWNTIME.read_text(encoding="utf-8")
    match = re.search(r"^INTERVAL_SECONDS=([\d.]+)$", script, flags=re.MULTILINE)
    assert match is not None, "check-zero-downtime.sh sem INTERVAL_SECONDS"
    assert 'sleep "$INTERVAL_SECONDS"' in script

    assert 1 / float(match.group(1)) <= _zone_rate_per_second("readyz")


def test_healthcheck_do_nginx_consulta_abaixo_do_limite_do_readyz() -> None:
    """O healthcheck do Nginx consulta o próprio `/readyz`, de 127.0.0.1."""
    nginx = json.loads(COOLIFY_APP.read_text(encoding="utf-8"))["services"]["nginx"]
    interval = re.fullmatch(r"(\d+)s", nginx["healthcheck"]["interval"])
    assert interval is not None

    assert 1 / int(interval.group(1)) <= _zone_rate_per_second("readyz")
