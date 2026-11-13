"""Menor privilégio nos containers da aplicação (issue #366).

`api`, `worker` e `maintenance` rodam como UID 10001: sem nenhuma capability e com
`no-new-privileges`, nem um binário setuid devolve privilégio a um processo comprometido. O Nginx
da imagem oficial sobe como root e precisa de quatro capabilities, medidas com a imagem real: sem
CHOWN o mestre não cria `/var/cache/nginx/*_temp` com o dono `nginx` e não sobe; sem SETUID e
SETGID não troca de usuário nos workers. NET_BIND_SERVICE fica porque a porta 80 só abre sem ela
se o daemon do host baixar `ip_unprivileged_port_start`, o que depende da versão do Docker da VPS.

O Nginx larga uma lista explícita, e não `ALL` mais `cap_add`, porque a política do host (ADR 034)
reprova qualquer `cap_add`.

Os destinos conferidos são os que sobem de verdade: o Compose local, o da aceitação sob gVisor e o
de produção (mesclados pelo próprio `docker compose config`) e o JSON publicado pelo Coolify.
"""

from typing import Any

import pytest

from tests.test_compose_host_policy import (
    ROOT,
    compose_config,
    coolify_document,
    production_environment,
)

HARDENED_APP_SERVICES = ("api", "worker", "maintenance")
NO_NEW_PRIVILEGES = "no-new-privileges:true"
# Capabilities que o Docker concede por padrão (moby, oci/caps/defaults.go).
DOCKER_DEFAULT_CAPS = frozenset(
    {
        "AUDIT_WRITE",
        "CHOWN",
        "DAC_OVERRIDE",
        "FOWNER",
        "FSETID",
        "KILL",
        "MKNOD",
        "NET_BIND_SERVICE",
        "NET_RAW",
        "SETFCAP",
        "SETGID",
        "SETPCAP",
        "SETUID",
        "SYS_CHROOT",
    }
)
NGINX_CAPS = frozenset({"CHOWN", "SETGID", "SETUID", "NET_BIND_SERVICE"})

COMPOSE_DESTINATIONS = {
    "local": ("compose.yaml", "compose.override.yaml"),
    "aceitacao-gvisor": ("compose.yaml", "compose.e2e-gvisor.yaml"),
    "producao": ("compose.yaml", "compose.prod.yaml"),
}


def _compose_services(files: tuple[str, ...]) -> dict[str, Any]:
    environment = production_environment() | {"GS_EXECUTOR_IMAGE": "placeholder"}
    config = compose_config([str(ROOT / name) for name in files], environment)
    services: dict[str, Any] = config["services"]
    return services


@pytest.fixture(scope="module")
def destinations() -> dict[str, dict[str, Any]]:
    configs = {name: _compose_services(files) for name, files in COMPOSE_DESTINATIONS.items()}
    configs["coolify"] = coolify_document("app.json")["services"]
    return configs


def _caps(values: list[str] | None) -> set[str]:
    return {value.upper().removeprefix("CAP_") for value in values or []}


@pytest.mark.parametrize("destination", [*COMPOSE_DESTINATIONS, "coolify"])
@pytest.mark.parametrize("service", HARDENED_APP_SERVICES)
def test_servico_da_aplicacao_sem_capabilities_e_sem_novos_privilegios(
    destinations: dict[str, dict[str, Any]], destination: str, service: str
) -> None:
    config = destinations[destination][service]

    assert _caps(config.get("cap_drop")) == {"ALL"}
    assert not config.get("cap_add")
    assert NO_NEW_PRIVILEGES in config.get("security_opt", [])


@pytest.mark.parametrize("destination", [*COMPOSE_DESTINATIONS, "coolify"])
def test_nginx_fica_so_com_as_capabilities_medidas(
    destinations: dict[str, dict[str, Any]], destination: str
) -> None:
    config = destinations[destination]["nginx"]

    assert _caps(config.get("cap_drop")) == DOCKER_DEFAULT_CAPS - NGINX_CAPS
    assert not config.get("cap_add")
    assert NO_NEW_PRIVILEGES in config.get("security_opt", [])


def test_nginx_mantem_o_net_raw_fora_na_borda(destinations: dict[str, dict[str, Any]]) -> None:
    # ADR 014: sem NET_RAW, o Nginx na rede edge não forja ARP para assumir o IP da borda.
    assert "NET_RAW" in _caps(destinations["producao"]["nginx"]["cap_drop"])
    assert "NET_RAW" in _caps(destinations["coolify"]["nginx"]["cap_drop"])


def test_storage_init_mantem_o_chown(destinations: dict[str, dict[str, Any]]) -> None:
    # Roda como root para criar a pasta dos objetos com o dono `app`: precisa de CHOWN.
    assert "cap_drop" not in destinations["producao"]["storage-init"]
    assert "cap_drop" not in destinations["coolify"]["storage-init"]
