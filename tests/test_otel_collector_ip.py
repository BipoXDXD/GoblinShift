"""IP fixo do OpenTelemetry Collector na rede `goblinshift_internal` (ADR 051, opção C1).

O Nginx vai mandar o access log por syslog para um IP, sem DNS, então o Collector precisa de um
endereço que não mude a cada recriação. Em produção a rede é externa e foi criada com a sub-rede
`172.30.0.0/24` (gateway `.1`); o `compose.yaml` declara a mesma sub-rede para o ambiente local.
O Docker só aceita `ipv4_address` dentro da sub-rede definida pelo usuário, e recusa o gateway e
o endereço de rede: este teste pega o erro antes do deploy manual no Coolify.
"""

import ipaddress
import json
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
INFRA = ROOT / "deploy" / "coolify" / "infra.json"
COMPOSE = ROOT / "compose.yaml"
COLLECTOR = "otel-collector"
NETWORK = "internal"
# Sub-rede e gateway medidos na VPS em 2026-11-10 (docker network inspect, só leitura).
INTERNAL_SUBNET = ipaddress.IPv4Network("172.30.0.0/24")
INTERNAL_GATEWAY = ipaddress.IPv4Address("172.30.0.1")


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
