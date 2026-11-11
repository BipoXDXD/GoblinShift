"""IP fixo do OpenTelemetry Collector na rede `goblinshift_internal` (ADR 051, opção C1).

O Nginx vai mandar o access log por syslog para um IP, sem DNS, então o Collector precisa de um
endereço que não mude a cada recriação. Em produção a rede é externa (nenhum arquivo do repositório
a cria); a sub-rede dela está fixada no `FORWARDED_ALLOW_IPS` do `app.json`, que só funciona se a
rede for exatamente essa, e o `compose.yaml` declara a mesma para o ambiente local. O Docker recusa
`ipv4_address` fora da sub-rede ou no gateway: este teste pega o erro antes do deploy manual no
Coolify.
"""

import ipaddress
import json
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
INFRA = ROOT / "deploy" / "coolify" / "infra.json"
APP = ROOT / "deploy" / "coolify" / "app.json"
COMPOSE = ROOT / "compose.yaml"
COLLECTOR = "otel-collector"
NETWORK = "internal"


def _internal_subnet() -> ipaddress.IPv4Network:
    """Sub-rede da `goblinshift_internal` segundo o `FORWARDED_ALLOW_IPS` do `app.json`.

    A lista tem a sub-rede interna e o IP do gateway de borda (`/32`); só a interna é uma rede de
    verdade. Todos os serviços do `app.json` que declaram a variável precisam concordar.
    """
    app: dict[str, Any] = json.loads(APP.read_text(encoding="utf-8"))
    subnets: set[ipaddress.IPv4Network] = set()
    for service in app["services"].values():
        allowed = service.get("environment", {}).get("FORWARDED_ALLOW_IPS")
        if allowed is None:
            continue
        networks = (ipaddress.IPv4Network(entry.strip()) for entry in allowed.split(","))
        subnets.update(network for network in networks if network.num_addresses > 1)
    assert len(subnets) == 1, f"FORWARDED_ALLOW_IPS precisa ter uma única sub-rede: {subnets}"
    return subnets.pop()


INTERNAL_SUBNET = _internal_subnet()
# Gateway padrão do Docker: o primeiro host da sub-rede (o .1 conferido na VPS em 2026-11-10).
INTERNAL_GATEWAY = next(INTERNAL_SUBNET.hosts())


def _collector_ip_in_infra() -> ipaddress.IPv4Address:
    infra: dict[str, Any] = json.loads(INFRA.read_text(encoding="utf-8"))
    networks = infra["services"][COLLECTOR]["networks"]
    return ipaddress.IPv4Address(networks[NETWORK]["ipv4_address"])


def _compose() -> dict[str, Any]:
    document: dict[str, Any] = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    return document


def test_ip_do_collector_esta_na_subrede_interna() -> None:
    assert _collector_ip_in_infra() in INTERNAL_SUBNET


def test_ip_do_collector_nao_e_gateway_nem_rede_nem_broadcast() -> None:
    reserved = {
        INTERNAL_SUBNET.network_address,
        INTERNAL_GATEWAY,
        INTERNAL_SUBNET.broadcast_address,
    }
    assert _collector_ip_in_infra() not in reserved


def test_ip_do_collector_e_igual_no_compose_local() -> None:
    networks = _compose()["services"][COLLECTOR]["networks"]
    assert ipaddress.IPv4Address(networks[NETWORK]["ipv4_address"]) == _collector_ip_in_infra()


def test_compose_local_declara_a_mesma_subrede_da_vps() -> None:
    ipam = _compose()["networks"][NETWORK]["ipam"]["config"]
    assert [ipaddress.IPv4Network(entry["subnet"]) for entry in ipam] == [INTERNAL_SUBNET]
