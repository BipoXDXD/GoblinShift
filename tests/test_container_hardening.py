"""Menor privilégio em todos os containers do projeto (issue #366).

`api`, `worker`, `maintenance` e `migrate` rodam como UID 10001, e o Collector como o 10001 da
própria imagem: sem nenhuma capability e com `no-new-privileges`, nem um binário setuid devolve
privilégio a um processo comprometido. O OpenObserve roda como root, mas só escreve no próprio
volume, do root: também sobe sem nenhuma.

Quem sobe como root e precisa de capability fica só com as medidas com a imagem real, cada uma
derrubando o serviço quando falta:

- Nginx: sem CHOWN o mestre não cria `/var/cache/nginx/*_temp` com o dono `nginx`; sem SETUID e
  SETGID não troca de usuário nos workers. NET_BIND_SERVICE fica porque a porta 80 só abre sem ela
  se o daemon do host baixar `ip_unprivileged_port_start`, o que depende da versão do Docker da VPS.
  KILL fica porque o mestre (root) só sinaliza os workers (`nginx`, outro UID) com ela: sem KILL, o
  SIGKILL ao worker que não sai depois do TERM falha com EPERM e o mestre fica preso até o Docker
  matar o container (medido com a imagem; o caso comum passa pelo canal interno e não acusa).
- Postgres: o entrypoint, como root, entrega o PGDATA ao `postgres` (CHOWN) e, no reinício,
  percorre o PGDATA de modo 700 do `postgres` (DAC_OVERRIDE) antes de trocar de usuário pelo
  `gosu` (SETUID e SETGID).
- `storage-init`: cria a pasta dos objetos (DAC_OVERRIDE: no volume nomeado, local e da CI, a raiz
  vem da imagem, de modo 700 do `app`) e a entrega ao `app` (CHOWN).
- `storage-mark`: cria a sentinela dentro da pasta de modo 700 do `app` (DAC_OVERRIDE).

Eles largam uma lista explícita, e não `ALL` mais `cap_add`, porque a política do host (ADR 034)
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

CAPABILITY_FREE_SERVICES = (
    "api",
    "worker",
    "maintenance",
    "migrate",
    "otel-collector",
    "openobserve",
)
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
NGINX_CAPS = frozenset({"CHOWN", "KILL", "SETGID", "SETUID", "NET_BIND_SERVICE"})
# Serviços que sobem como root, com as capabilities medidas que ficam.
ROOT_SERVICE_CAPS = {
    "nginx": NGINX_CAPS,
    "postgres": frozenset({"CHOWN", "DAC_OVERRIDE", "SETGID", "SETUID"}),
    "storage-init": frozenset({"CHOWN", "DAC_OVERRIDE"}),
    "storage-mark": frozenset({"DAC_OVERRIDE"}),
}
# O Coolify não publica a sentinela: ela só roda pelo deploy/storage-mark.sh, com o Compose.
COMPOSE_ONLY_SERVICES = frozenset({"storage-mark"})

COMPOSE_DESTINATIONS = {
    "local": ("compose.yaml", "compose.override.yaml"),
    "aceitacao-gvisor": ("compose.yaml", "compose.e2e-gvisor.yaml"),
    "producao": ("compose.yaml", "compose.prod.yaml"),
}


def _compose_services(files: tuple[str, ...]) -> dict[str, Any]:
    # O perfil põe a sentinela (`storage-mark`), fora do `up`, na configuração conferida.
    environment = production_environment() | {
        "GS_EXECUTOR_IMAGE": "placeholder",
        "COMPOSE_PROFILES": "storage-mark",
    }
    config = compose_config([str(ROOT / name) for name in files], environment)
    services: dict[str, Any] = config["services"]
    return services


@pytest.fixture(scope="module")
def destinations() -> dict[str, dict[str, Any]]:
    configs = {name: _compose_services(files) for name, files in COMPOSE_DESTINATIONS.items()}
    configs["coolify"] = (
        coolify_document("app.json")["services"] | coolify_document("infra.json")["services"]
    )
    return configs


def _caps(values: list[str] | None) -> set[str]:
    return {value.upper().removeprefix("CAP_") for value in values or []}


@pytest.mark.parametrize("destination", [*COMPOSE_DESTINATIONS, "coolify"])
@pytest.mark.parametrize("service", CAPABILITY_FREE_SERVICES)
def test_servico_sem_capabilities_e_sem_novos_privilegios(
    destinations: dict[str, dict[str, Any]], destination: str, service: str
) -> None:
    config = destinations[destination][service]

    assert _caps(config.get("cap_drop")) == {"ALL"}
    assert not config.get("cap_add")
    assert NO_NEW_PRIVILEGES in config.get("security_opt", [])


@pytest.mark.parametrize(
    ("destination", "service"),
    [
        (destination, service)
        for service in ROOT_SERVICE_CAPS
        for destination in [*COMPOSE_DESTINATIONS, "coolify"]
        if destination != "coolify" or service not in COMPOSE_ONLY_SERVICES
    ],
)
def test_servico_root_fica_so_com_as_capabilities_medidas(
    destinations: dict[str, dict[str, Any]], destination: str, service: str
) -> None:
    config = destinations[destination][service]

    assert _caps(config.get("cap_drop")) == DOCKER_DEFAULT_CAPS - ROOT_SERVICE_CAPS[service]
    assert not config.get("cap_add")
    assert NO_NEW_PRIVILEGES in config.get("security_opt", [])


def test_nginx_mantem_o_net_raw_fora_na_borda(destinations: dict[str, dict[str, Any]]) -> None:
    # ADR 014: sem NET_RAW, o Nginx na rede edge não forja ARP para assumir o IP da borda.
    assert "NET_RAW" in _caps(destinations["producao"]["nginx"]["cap_drop"])
    assert "NET_RAW" in _caps(destinations["coolify"]["nginx"]["cap_drop"])


def test_storage_init_mantem_o_chown(destinations: dict[str, dict[str, Any]]) -> None:
    # Roda como root para criar a pasta dos objetos com o dono `app`: precisa de CHOWN.
    assert "CHOWN" not in _caps(destinations["producao"]["storage-init"].get("cap_drop"))
    assert "CHOWN" not in _caps(destinations["coolify"]["storage-init"].get("cap_drop"))
