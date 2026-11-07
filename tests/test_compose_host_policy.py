"""Fitness function do Compose de produção contra o host (ADR 034).

Quem controla o Compose implantado controla o daemon do Docker da VPS compartilhada, ou seja, tem
root sobre ela (ADR 034). O teste não impede um atacante, que altera o teste no mesmo commit; ele
reprova o erro honesto que abriria o host, conferido sobre a configuração **mesclada e
interpolada** pelo próprio `docker compose config`, com as variáveis que o deploy/compose.sh
exporta na VPS.

A produção executa os Compose em JSON de `deploy/coolify/`, publicados pelo Coolify. Eles passam
pela mesma política, também depois do `docker compose config`, que resolve caminhos relativos,
sintaxe curta de montagem e o valor padrão de `${VAR:-...}`.
"""

import copy
import json
import os
import re
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from tests.compose_host_policy import (
    HostPaths,
    coolify_policy_violations,
    host_policy_violations,
    is_protected_volume,
)

ROOT = Path(__file__).resolve().parent.parent
PRODUCTION_FILES = (ROOT / "compose.yaml", ROOT / "compose.prod.yaml")
COMPOSE_SCRIPT = ROOT / "deploy" / "compose.sh"
HOST_ROOT = "/srv/goblinshift"
CONFIG_TIMEOUT_SECONDS = 60
REQUIRED_VARIABLE = re.compile(r"\$\{([A-Z_][A-Z0-9_]*):\?")
# `export GS_PG_DATA="$root/pg/data"` no deploy/compose.sh: os caminhos do host saem dele, para
# o teste não divergir do que a VPS usa.
EXPORTED_PATH = re.compile(r'^export (GS_[A-Z_]+)="\$root(/[^"]*)"$', re.MULTILINE)
EXPORTED_VALUE = re.compile(r"^export (GS_[A-Z_]+)=([a-z]+)$", re.MULTILINE)

HOST = HostPaths(project_dir=str(ROOT))


def production_environment() -> dict[str, str]:
    """As variáveis do deploy/compose.sh, mais um valor fictício para cada `${VAR:?}`."""
    required = {
        name
        for compose_file in PRODUCTION_FILES
        for name in REQUIRED_VARIABLE.findall(compose_file.read_text())
    }
    script = COMPOSE_SCRIPT.read_text()
    exported = {name: f"{HOST_ROOT}{path}" for name, path in EXPORTED_PATH.findall(script)}
    exported |= dict(EXPORTED_VALUE.findall(script))
    return {
        "PATH": os.environ["PATH"],
        "HOME": os.environ.get("HOME", "/"),
        **dict.fromkeys(required, "placeholder"),
        **exported,
    }


def compose_config(
    files: list[str], environment: dict[str, str], stdin: str | None = None
) -> dict[str, Any]:
    docker = shutil.which("docker")
    assert docker is not None, "o fitness test do Compose precisa do docker (com o plugin compose)"
    arguments = [arg for file in files for arg in ("-f", file)]
    result = subprocess.run(  # noqa: S603 - argumentos montados pelo teste, sem shell
        [
            *(docker, "compose", "--project-directory", str(ROOT), *arguments),
            *("config", "--format", "json"),
        ],
        input=stdin,
        capture_output=True,
        text=True,
        env=environment,
        check=False,
        timeout=CONFIG_TIMEOUT_SECONDS,
    )
    assert result.returncode == 0, result.stderr
    config: dict[str, Any] = json.loads(result.stdout)
    return config


def merged_config(*extra_files: Path) -> dict[str, Any]:
    files = [str(path) for path in (*PRODUCTION_FILES, *extra_files)]
    return compose_config(files, production_environment())


def test_o_ambiente_reproduz_os_caminhos_do_compose_sh() -> None:
    environment = production_environment()

    assert environment["GS_PG_DATA"] == "/srv/goblinshift/pg/data"
    assert environment["GS_STORAGE_DATA"] == "/srv/goblinshift/storage"
    assert environment["GS_TELEMETRY_DATA"] == "/srv/goblinshift/telemetry/data"
    assert environment["GS_SECRETS_DIR"] == "/srv/goblinshift/secrets"
    assert environment["GS_ENVIRONMENT"] == "production"


def test_o_compose_de_producao_respeita_a_politica_do_host() -> None:
    config = merged_config()

    assert host_policy_violations(config, HOST) == []


def test_privileged_injetado_no_compose_de_producao_e_reprovado(tmp_path: Path) -> None:
    injected = tmp_path / "compose.injected.yaml"
    injected.write_text("services:\n  worker:\n    privileged: true\n")

    violations = host_policy_violations(merged_config(injected), HOST)

    assert violations == ["worker: privileged"]


# --- A política sobre configurações já mescladas (o formato do `docker compose config`) ---------


def config_with(service: dict[str, Any], **top_level: Any) -> dict[str, Any]:
    return {"services": {"app": {"image": "x", **service}}, **top_level}


def bind(source: str, target: str = "/mnt", *, read_only: bool = False) -> dict[str, Any]:
    mount: dict[str, Any] = {"type": "bind", "source": source, "target": target}
    if read_only:
        mount["read_only"] = True
    return mount


@pytest.mark.parametrize(
    ("service", "expected"),
    [
        ({"privileged": True}, "app: privileged"),
        ({"cap_add": ["SYS_ADMIN"]}, "app: cap_add"),
        ({"pid": "host"}, "app: pid=host"),
        ({"network_mode": "host"}, "app: network_mode=host"),
        ({"ipc": "host"}, "app: ipc=host"),
        ({"userns_mode": "host"}, "app: userns_mode=host"),
        ({"uts": "host"}, "app: uts=host"),
        # Entrar no namespace de outro container alcança containers de outro projeto da VPS.
        ({"network_mode": "container:stillon-db"}, "app: network_mode=container:stillon-db"),
        ({"pid": "container:stillon-db"}, "app: pid=container:stillon-db"),
        ({"ipc": "container:stillon-db"}, "app: ipc=container:stillon-db"),
        ({"devices": [{"source": "/dev/sda", "target": "/dev/sda"}]}, "app: devices"),
        ({"security_opt": ["seccomp:unconfined"]}, "app: security_opt seccomp:unconfined"),
        ({"security_opt": ["seccomp=unconfined"]}, "app: security_opt seccomp=unconfined"),
        ({"security_opt": ["apparmor:unconfined"]}, "app: security_opt apparmor:unconfined"),
        ({"security_opt": ["apparmor=unconfined"]}, "app: security_opt apparmor=unconfined"),
        ({"security_opt": ["label:disable"]}, "app: security_opt label:disable"),
        ({"security_opt": ["label=disable"]}, "app: security_opt label=disable"),
        ({"security_opt": ["systempaths=unconfined"]}, "app: security_opt systempaths=unconfined"),
        ({"volumes_from": ["stillon-app"]}, "app: volumes_from"),
        ({"cgroup_parent": "/system.slice"}, "app: cgroup_parent"),
        ({"cgroup": "host"}, "app: cgroup=host"),
        ({"use_api_socket": True}, "app: use_api_socket"),
    ],
    ids=lambda value: value if isinstance(value, str) else None,
)
def test_opcao_que_abre_o_host_e_reprovada(service: dict[str, Any], expected: str) -> None:
    assert host_policy_violations(config_with(service), HOST) == [expected]


@pytest.mark.parametrize(
    "service",
    [
        {"security_opt": ["no-new-privileges:true"]},
        {"cap_drop": ["ALL"]},
        {"network_mode": "none"},
        {"pid": "service:worker"},
        {"privileged": False},
        {"cap_add": []},
        {"use_api_socket": False},
    ],
)
def test_opcao_que_nao_abre_o_host_e_aceita(service: dict[str, Any]) -> None:
    assert host_policy_violations(config_with(service), HOST) == []


@pytest.mark.parametrize(
    "source",
    [
        "/var/run/docker.sock",
        "/run/docker.sock",
        "/",
        "/etc",
        "/srv/edge",
        "/srv/goblinshift",
        "/srv/goblinshift/secrets",
        "/srv/goblinshift/storage/../secrets",
        "/srv/goblinshift/storage-outro",
        "/home/goblinshift/.ssh",
    ],
)
def test_bind_fora_da_allowlist_e_reprovado(source: str) -> None:
    config = config_with({"volumes": [bind(source)]})

    assert host_policy_violations(config, HOST) == [f"app: bind {source}"]


@pytest.mark.parametrize(
    "source",
    [
        "/srv/goblinshift/storage",
        "/srv/goblinshift/pg/data",
        "/srv/goblinshift/telemetry/data",
        "/srv/goblinshift/exec-work",
        "/run/goblinshift",
    ],
)
def test_bind_de_escrita_da_allowlist_e_aceito(source: str) -> None:
    config = config_with({"volumes": [bind(source)]})

    assert host_policy_violations(config, HOST) == []


@pytest.mark.parametrize(
    "source", ["/srv/goblinshift/pg", "/srv/goblinshift/telemetry", f"{ROOT}/docker/x.yaml"]
)
def test_bind_so_de_leitura_exige_read_only(source: str) -> None:
    writable = config_with({"volumes": [bind(source)]})
    read_only = config_with({"volumes": [bind(source, read_only=True)]})

    assert host_policy_violations(writable, HOST) == [f"app: bind {source}"]
    assert host_policy_violations(read_only, HOST) == []


def test_bind_do_repositorio_fora_de_docker_e_reprovado() -> None:
    source = f"{ROOT}/deploy/provision.sh"
    config = config_with({"volumes": [bind(source, read_only=True)]})

    assert host_policy_violations(config, HOST) == [f"app: bind {source}"]


def test_volume_nomeado_e_tmpfs_sao_aceitos() -> None:
    config = config_with(
        {
            "volumes": [
                {"type": "volume", "source": "pgdata", "target": "/data"},
                {"type": "tmpfs", "target": "/tmp"},  # noqa: S108 - destino dentro do container
            ]
        },
        volumes={"pgdata": {}},
    )

    assert host_policy_violations(config, HOST) == []


def test_volume_nomeado_com_driver_opts_e_reprovado() -> None:
    # O driver `local` com `o: bind` e `device: /` monta o host por um volume "nomeado".
    config = config_with(
        {"volumes": [{"type": "volume", "source": "raiz", "target": "/host"}]},
        volumes={"raiz": {"driver_opts": {"type": "none", "o": "bind", "device": "/"}}},
    )

    assert host_policy_violations(config, HOST) == ["volume raiz: driver_opts"]


@pytest.mark.parametrize(
    "volume_name",
    ["coolify-db", "edge_caddy_data", "goblinshift", "goblinshift-pg", "x_goblinshift_pg"],
)
def test_volume_com_nome_fora_do_projeto_e_reprovado(volume_name: str) -> None:
    config = config_with({}, name="goblinshift", volumes={"alias": {"name": volume_name}})

    assert host_policy_violations(config, HOST) == [
        f"volume alias: nome {volume_name} fora do projeto goblinshift"
    ]


def test_volume_externo_com_nome_fora_do_projeto_aponta_os_dois_problemas() -> None:
    config = config_with(
        {}, name="goblinshift", volumes={"alias": {"external": True, "name": "coolify-db"}}
    )

    assert host_policy_violations(config, HOST) == [
        "volume alias: external",
        "volume alias: nome coolify-db fora do projeto goblinshift",
    ]


def test_volume_com_nome_e_sem_projeto_e_reprovado() -> None:
    config = config_with({}, volumes={"alias": {"name": "coolify-db"}})

    assert host_policy_violations(config, HOST) == [
        "volume alias: nome coolify-db fora do projeto None"
    ]


@pytest.mark.parametrize("volume", [{"name": "goblinshift_pgdata"}, {"name": "goblinshift_x"}, {}])
def test_volume_do_proprio_projeto_e_aceito(volume: dict[str, Any]) -> None:
    config = config_with({}, name="goblinshift", volumes={"pgdata": volume})

    assert host_policy_violations(config, HOST) == []


def test_o_compose_de_producao_fica_no_projeto_goblinshift() -> None:
    assert merged_config()["name"] == "goblinshift"


def test_volume_com_nome_alheio_injetado_no_compose_de_producao_e_reprovado(tmp_path: Path) -> None:
    injected = tmp_path / "compose.injected.yaml"
    injected.write_text(
        "services:\n  worker:\n    volumes: ['alias:/alias']\n"
        "volumes:\n  alias:\n    name: coolify-db\n"
    )

    violations = host_policy_violations(merged_config(injected), HOST)

    assert violations == ["volume alias: nome coolify-db fora do projeto goblinshift"]


EDGE_VOLUME = {"external": True, "name": "edge_caddy_data"}


def edge_config(key: str, volume: dict[str, Any], *, service: str = "proxy") -> dict[str, Any]:
    mount = {"type": "volume", "source": key, "target": "/data"}
    return {"name": "edge", "services": {service: {"volumes": [mount]}}, "volumes": {key: volume}}


def test_excecao_do_adr_034_aceita_o_volume_registrado() -> None:
    assert host_policy_violations(edge_config("caddy_data", EDGE_VOLUME), HOST) == []


def test_excecao_do_adr_034_nao_vale_em_outro_servico() -> None:
    config = edge_config("caddy_data", EDGE_VOLUME, service="outro")

    assert host_policy_violations(config, HOST) == ["outro: monta caddy_data"]


def test_excecao_do_adr_034_nao_vale_em_outra_chave() -> None:
    assert host_policy_violations(edge_config("dados", EDGE_VOLUME), HOST) == [
        "volume dados: external",
        "volume dados: nome reservado edge_caddy_data",
    ]


def test_excecao_do_adr_034_nao_vale_em_outro_projeto() -> None:
    config = {**edge_config("caddy_data", EDGE_VOLUME), "name": "goblinshift"}

    assert host_policy_violations(config, HOST) == [
        "volume caddy_data: external",
        "volume caddy_data: nome edge_caddy_data fora do projeto goblinshift",
    ]


def test_excecao_do_adr_034_exige_external_verdadeiro() -> None:
    volume = {"external": False, "name": "edge_caddy_data"}

    assert host_policy_violations(edge_config("caddy_data", volume), HOST) == [
        "volume caddy_data: nome reservado edge_caddy_data"
    ]


def test_excecao_do_adr_034_nao_dispensa_driver_opts() -> None:
    volume = {**EDGE_VOLUME, "driver_opts": {"type": "none", "o": "bind", "device": "/"}}

    assert host_policy_violations(edge_config("caddy_data", volume), HOST) == [
        "volume caddy_data: driver_opts"
    ]


def test_chave_fora_da_excecao_nao_e_protegida_nem_sem_nome() -> None:
    # Sem a chave na exceção, `None == None` aceitaria um volume sem `name`.
    assert not is_protected_volume("dados", {"external": True}, "edge")


def test_volume_externo_e_reprovado() -> None:
    # Volume de outro projeto da VPS (ADR 011).
    config = config_with(
        {"volumes": [{"type": "volume", "source": "alheio", "target": "/data"}]},
        volumes={"alheio": {"external": True}},
    )

    assert host_policy_violations(config, HOST) == ["volume alheio: external"]


@pytest.mark.parametrize("mount_type", ["npipe", "cluster", "image"])
def test_outros_tipos_de_montagem_sao_reprovados(mount_type: str) -> None:
    config = config_with({"volumes": [{"type": mount_type, "source": "x", "target": "/x"}]})

    assert host_policy_violations(config, HOST) == [f"app: mount {mount_type}"]


def test_secret_fora_do_diretorio_de_segredos_e_reprovado() -> None:
    config = config_with({}, secrets={"sombra": {"file": "/etc/shadow"}})

    assert host_policy_violations(config, HOST) == ["secret sombra: /etc/shadow"]


def test_secret_no_diretorio_de_segredos_e_aceito() -> None:
    config = config_with({}, secrets={"db": {"file": "/srv/goblinshift/secrets/db_password"}})

    assert host_policy_violations(config, HOST) == []


def test_secret_que_escapa_do_diretorio_de_segredos_e_reprovado() -> None:
    source = "/srv/goblinshift/secrets/../app/.git/config"
    config = config_with({}, secrets={"fuga": {"file": source}})

    assert host_policy_violations(config, HOST) == [f"secret fuga: {source}"]


def test_config_fora_de_docker_do_repositorio_e_reprovado() -> None:
    config = config_with({}, configs={"c": {"file": "/etc/passwd"}})

    assert host_policy_violations(config, HOST) == ["config c: /etc/passwd"]


def test_config_em_docker_do_repositorio_e_aceito() -> None:
    config = config_with({}, configs={"c": {"file": f"{ROOT}/docker/nginx/x.conf"}})

    assert host_policy_violations(config, HOST) == []


def test_todas_as_violacoes_sao_listadas() -> None:
    config = {
        "services": {
            "a": {"privileged": True, "volumes": [bind("/")]},
            "b": {"cap_add": ["NET_ADMIN"]},
        }
    }

    assert host_policy_violations(config, HOST) == ["a: privileged", "a: bind /", "b: cap_add"]


# --- O Compose em JSON que a produção executa (deploy/coolify/) ---------------------------------


COOLIFY_DIR = ROOT / "deploy" / "coolify"
DIGEST = "a" * 64
GATEWAY_SERVICE = "proxy"
CADDYFILE = f"{ROOT}/docker/gateway/Caddyfile"
SITES = f"{ROOT}/docker/gateway/sites"
# A política não olha o alvo das montagens: é o que este teste acrescenta para o gateway.
GATEWAY_MOUNTS = [
    ("bind", CADDYFILE, "/etc/caddy/Caddyfile", True),
    ("bind", SITES, "/etc/caddy/conf.d", True),
    ("volume", "caddy_data", "/data", False),
    ("volume", "caddy_config", "/config", False),
]
# O projeto é a referência do nome dos volumes: trocá-lo mudaria o que conta como "do projeto".
EXPECTED_PROJECTS = {"app.json": "goblinshift", "infra.json": "goblinshift", "gateway.json": "edge"}
COOLIFY_NAMES = tuple(EXPECTED_PROJECTS)
# Cada sabotagem é a mudança no serviço e o problema que a política precisa apontar.
SABOTAGES: list[tuple[dict[str, Any], str]] = [
    ({"privileged": True}, "privileged"),
    ({"cap_add": ["SYS_ADMIN"]}, "cap_add"),
    ({"network_mode": "host"}, "network_mode=host"),
    ({"pid": "host"}, "pid=host"),
    ({"image": "caddy:2"}, "image sem digest: caddy:2"),
    ({"image": "${GS_APP_IMAGE:-ghcr.io/x/y:latest}"}, "image sem digest: ghcr.io/x/y:latest"),
    ({"build": "."}, "build"),
    ({"use_api_socket": True}, "use_api_socket"),
]
SABOTAGED_MOUNTS: list[tuple[Any, str]] = [
    ("/var/run/docker.sock:/var/run/docker.sock", "bind /var/run/docker.sock"),
    ("/run/docker.sock:/var/run/docker.sock:ro", "bind /run/docker.sock"),
    ("/:/host", "bind /"),
    ("/:/host:ro", "bind /"),
    ({"type": "bind", "source": "/", "target": "/host"}, "bind /"),
]


def coolify_document(name: str) -> dict[str, Any]:
    document: dict[str, Any] = json.loads((COOLIFY_DIR / name).read_text(encoding="utf-8"))
    return document


def rendered(document: dict[str, Any]) -> dict[str, Any]:
    """O JSON como o Compose o entende, lido da memória: nenhum arquivo real é alterado."""
    text = json.dumps(document)
    environment = {
        "PATH": os.environ["PATH"],
        "HOME": os.environ.get("HOME", "/"),
        **dict.fromkeys(REQUIRED_VARIABLE.findall(text), "placeholder"),
    }
    return compose_config(["-"], environment, stdin=text)


def problems_of(document: dict[str, Any]) -> list[str]:
    return coolify_policy_violations(rendered(document), HOST)


def first_service(document: dict[str, Any]) -> str:
    names: list[str] = list(document["services"])
    return names[0]


def sabotaged(document: dict[str, Any], service: str, changes: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(document)
    target = result["services"][service]
    if "network_mode" in changes:
        # O Compose recusa `network_mode` junto de `networks`.
        del target["networks"]
    target.update(changes)
    return result


def with_mount(document: dict[str, Any], service: str, mount: Any) -> dict[str, Any]:
    current = document["services"][service].get("volumes", [])
    return sabotaged(document, service, {"volumes": [*current, mount]})


def gateway_mount(document: dict[str, Any], index: int) -> dict[str, Any]:
    mount: dict[str, Any] = document["services"][GATEWAY_SERVICE]["volumes"][index]
    return mount


def bind_without_read_only(document: dict[str, Any], index: int) -> None:
    del gateway_mount(document, index)["read_only"]


def bind_with_target(document: dict[str, Any], index: int, target: str) -> None:
    gateway_mount(document, index)["target"] = target


def service_mounting(document: dict[str, Any], mount: str) -> None:
    document["services"]["outro"] = {"image": f"caddy@sha256:{DIGEST}", "volumes": [mount]}


def proxy_with_volumes(document: dict[str, Any], *mounts: str) -> None:
    document["services"][GATEWAY_SERVICE]["volumes"][2:] = list(mounts)


def renamed_volume(document: dict[str, Any], volume: str, name: str) -> None:
    document["volumes"][volume]["name"] = name


def not_external(document: dict[str, Any], volume: str) -> None:
    del document["volumes"][volume]["external"]


def declared_alias(document: dict[str, Any], name: str, *, external: bool = False) -> None:
    alias: dict[str, Any] = {"name": name}
    if external:
        alias["external"] = True
    document["volumes"] = {**document.get("volumes", {}), "alias": alias}


def alias_in_other_service(document: dict[str, Any], name: str, *, external: bool = False) -> None:
    declared_alias(document, name, external=external)
    service_mounting(document, "alias:/data")


def alias_in_proxy(document: dict[str, Any], name: str) -> None:
    declared_alias(document, name)
    document["services"][GATEWAY_SERVICE]["volumes"].append("alias:/alias")


def gateway_mounts(config: dict[str, Any]) -> list[tuple[str, str, str, bool]]:
    return [
        (mount["type"], mount["source"], mount["target"], bool(mount.get("read_only")))
        for mount in config["services"][GATEWAY_SERVICE]["volumes"]
    ]


GatewayMutation = Callable[[dict[str, Any]], None]
GATEWAY_POLICY_MUTATIONS: list[tuple[str, GatewayMutation, list[str]]] = [
    (
        "Caddyfile_gravavel",
        lambda document: bind_without_read_only(document, 0),
        [f"proxy: bind {CADDYFILE}"],
    ),
    (
        "sites_gravavel",
        lambda document: bind_without_read_only(document, 1),
        [f"proxy: bind {SITES}"],
    ),
    (
        "caddy_data_de_outro_projeto",
        lambda document: renamed_volume(document, "caddy_data", "coolify-db"),
        ["volume caddy_data: external", "volume caddy_data: nome coolify-db fora do projeto edge"],
    ),
    (
        "caddy_config_de_outro_projeto",
        lambda document: renamed_volume(document, "caddy_config", "coolify-db"),
        [
            "volume caddy_config: external",
            "volume caddy_config: nome coolify-db fora do projeto edge",
        ],
    ),
    (
        "chave_caddy_data_com_o_nome_do_outro_volume",
        lambda document: renamed_volume(document, "caddy_data", "edge_caddy_config"),
        ["volume caddy_data: external", "volume caddy_data: nome reservado edge_caddy_config"],
    ),
    (
        "caddy_data_sem_external",
        lambda document: not_external(document, "caddy_data"),
        ["volume caddy_data: nome reservado edge_caddy_data"],
    ),
    (
        "caddy_config_sem_external",
        lambda document: not_external(document, "caddy_config"),
        ["volume caddy_config: nome reservado edge_caddy_config"],
    ),
    (
        "alias_do_volume_real_em_outro_servico",
        lambda document: alias_in_other_service(document, "edge_caddy_data"),
        ["volume alias: nome reservado edge_caddy_data"],
    ),
    (
        "alias_externo_do_volume_real_em_outro_servico",
        lambda document: alias_in_other_service(document, "edge_caddy_config", external=True),
        ["volume alias: external", "volume alias: nome reservado edge_caddy_config"],
    ),
    (
        "alias_do_volume_real_no_proxy",
        lambda document: alias_in_proxy(document, "edge_caddy_data"),
        ["volume alias: nome reservado edge_caddy_data"],
    ),
    (
        "alias_de_outro_projeto_no_proxy",
        lambda document: alias_in_proxy(document, "coolify-db"),
        ["volume alias: nome coolify-db fora do projeto edge"],
    ),
    (
        "caddy_data_em_outro_servico",
        lambda document: service_mounting(document, "caddy_data:/data"),
        ["outro: monta caddy_data"],
    ),
    (
        "caddy_config_em_outro_servico",
        lambda document: service_mounting(document, "caddy_config:/config"),
        ["outro: monta caddy_config"],
    ),
]
GATEWAY_MOUNT_MUTATIONS: list[tuple[str, GatewayMutation]] = [
    ("Caddyfile_em_outro_alvo", lambda document: bind_with_target(document, 0, "/etc/passwd")),
    ("sites_em_outro_alvo", lambda document: bind_with_target(document, 1, "/srv")),
    (
        "caddy_data_em_outro_alvo",
        lambda document: proxy_with_volumes(document, "caddy_data:/outro", "caddy_config:/config"),
    ),
    (
        "caddy_config_so_leitura",
        lambda document: proxy_with_volumes(
            document, "caddy_data:/data", "caddy_config:/config:ro"
        ),
    ),
]


def test_o_gateway_mantem_as_montagens_registradas() -> None:
    assert gateway_mounts(rendered(coolify_document("gateway.json"))) == GATEWAY_MOUNTS


@pytest.mark.parametrize(
    "mutate",
    [mutate for _, mutate in GATEWAY_MOUNT_MUTATIONS],
    ids=[case for case, _ in GATEWAY_MOUNT_MUTATIONS],
)
def test_mudanca_nas_montagens_do_gateway_e_reprovada(mutate: GatewayMutation) -> None:
    document = coolify_document("gateway.json")
    mutate(document)

    assert gateway_mounts(rendered(document)) != GATEWAY_MOUNTS


@pytest.mark.parametrize(
    ("mutate", "problems"),
    [(mutate, problems) for _, mutate, problems in GATEWAY_POLICY_MUTATIONS],
    ids=[case for case, _, _ in GATEWAY_POLICY_MUTATIONS],
)
def test_furo_na_excecao_do_gateway_e_reprovado(
    mutate: GatewayMutation, problems: list[str]
) -> None:
    document = coolify_document("gateway.json")
    mutate(document)

    assert problems_of(document) == problems


def test_gateway_sonda_a_saude_a_cada_segundo_na_subida() -> None:
    # O Traefik só roteia container healthy: sem sondagem rápida, a primeira sonda vem no `interval`
    # e cada recriação do gateway deixa os sites fora do ar por esse tempo.
    healthcheck = rendered(coolify_document("gateway.json"))["services"]["proxy"]["healthcheck"]

    assert (healthcheck["start_period"], healthcheck["start_interval"]) == ("30s", "1s")


def project_problems(config: dict[str, Any], name: str) -> list[str]:
    return [] if config["name"] == EXPECTED_PROJECTS[name] else [f"projeto {config['name']}"]


@pytest.mark.parametrize("name", COOLIFY_NAMES)
def test_cada_json_mantem_o_projeto_registrado(name: str) -> None:
    assert project_problems(rendered(coolify_document(name)), name) == []


@pytest.mark.parametrize("name", COOLIFY_NAMES)
def test_trocar_o_projeto_do_json_e_reprovado(name: str) -> None:
    document = coolify_document(name)
    document["name"] = "coolify"

    assert project_problems(rendered(document), name) == ["projeto coolify"]


@pytest.mark.parametrize(
    ("name", "volume_name"),
    [
        ("app.json", "coolify-db"),
        ("app.json", "edge_caddy_data"),
        ("infra.json", "coolify-db"),
        ("infra.json", "edge_caddy_data"),
        ("gateway.json", "coolify-db"),
        ("gateway.json", "goblinshift_pgdata"),
    ],
)
def test_volume_nao_externo_com_nome_alheio_no_json_e_reprovado(
    name: str, volume_name: str
) -> None:
    document = coolify_document(name)
    alias_in_other_service(document, volume_name)

    assert problems_of(document) == [
        f"volume alias: nome {volume_name} fora do projeto {EXPECTED_PROJECTS[name]}"
    ]


def test_todo_json_do_coolify_passa_pela_politica() -> None:
    assert sorted(path.name for path in COOLIFY_DIR.glob("*.json")) == sorted(COOLIFY_NAMES)


@pytest.mark.parametrize("name", COOLIFY_NAMES)
def test_o_compose_em_json_da_producao_respeita_a_politica_do_host(name: str) -> None:
    config = rendered(coolify_document(name))

    assert coolify_policy_violations(config, HOST) == []


@pytest.mark.parametrize("name", COOLIFY_NAMES)
def test_o_compose_nao_esconde_servico_da_politica(name: str) -> None:
    # Um serviço atrás de `profiles` sairia do `docker compose config` sem ser verificado.
    document = coolify_document(name)

    assert set(rendered(document)["services"]) == set(document["services"])


@pytest.mark.parametrize("name", COOLIFY_NAMES)
@pytest.mark.parametrize(
    ("changes", "problem"), SABOTAGES, ids=[problem for _, problem in SABOTAGES]
)
def test_sabotagem_em_opcao_do_json_e_reprovada(
    name: str, changes: dict[str, Any], problem: str
) -> None:
    document = coolify_document(name)
    service = first_service(document)

    problems = problems_of(sabotaged(document, service, changes))

    assert problems == [f"{service}: {problem}"]


@pytest.mark.parametrize("name", COOLIFY_NAMES)
@pytest.mark.parametrize(
    ("mount", "problem"),
    SABOTAGED_MOUNTS,
    ids=[str(mount) for mount, _ in SABOTAGED_MOUNTS],
)
def test_sabotagem_em_montagem_do_json_e_reprovada(name: str, mount: Any, problem: str) -> None:
    document = coolify_document(name)
    service = first_service(document)

    problems = problems_of(with_mount(document, service, mount))

    assert problems == [f"{service}: {problem}"]


@pytest.mark.parametrize("name", COOLIFY_NAMES)
def test_a_politica_alcanca_todos_os_servicos_do_json(name: str) -> None:
    document = coolify_document(name)
    services = list(document["services"])
    for service in services:
        document = sabotaged(document, service, {"privileged": True})

    problems = problems_of(document)

    assert sorted(problems) == sorted(f"{service}: privileged" for service in services)


@pytest.mark.parametrize(
    "image",
    [
        f"caddy@sha256:{DIGEST}",
        f"caddy:2@sha256:{DIGEST}",
        f"ghcr.io/org/app:sha-0123@sha256:{DIGEST}",
    ],
)
def test_imagem_com_digest_e_aceita(image: str) -> None:
    assert coolify_policy_violations(config_with({"image": image}), HOST) == []


@pytest.mark.parametrize(
    "image",
    [
        "caddy",
        "caddy:2",
        "caddy:latest",
        f"caddy@sha256:{DIGEST[:-1]}",
        f"caddy@sha256:{DIGEST}0",
        f"caddy@sha256:{DIGEST.upper()}",
        f"caddy@sha512:{DIGEST}",
        f"@sha256:{DIGEST}",
    ],
)
def test_imagem_sem_digest_valido_e_reprovada(image: str) -> None:
    config = config_with({"image": image})

    assert coolify_policy_violations(config, HOST) == [f"app: image sem digest: {image}"]


def test_servico_sem_imagem_e_reprovado() -> None:
    config = {"services": {"app": {"command": "x"}}}

    assert coolify_policy_violations(config, HOST) == ["app: image sem digest: ausente"]


def test_build_com_imagem_fixada_e_reprovado() -> None:
    # A imagem local construída na VPS tomaria o lugar da que o digest fixou.
    config = config_with({"image": f"caddy@sha256:{DIGEST}", "build": {"context": "."}})

    assert coolify_policy_violations(config, HOST) == ["app: build"]


def test_as_regras_de_imagem_nao_alcancam_o_compose_da_vps() -> None:
    config = config_with({"image": "caddy:2", "build": {"context": "."}})

    assert host_policy_violations(config, HOST) == []
