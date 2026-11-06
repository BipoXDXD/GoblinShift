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
from pathlib import Path
from typing import Any

import pytest

from tests.compose_host_policy import (
    HostPaths,
    coolify_policy_violations,
    host_policy_violations,
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
# O que o gateway tem fora da política, sem exceção na própria política. A lista exata mantém o
# resto da regra valendo para ele: um bind ou volume a mais reprova o teste.
GATEWAY_DEVIATIONS = (
    # Configuração do Caddy versionada, só para leitura, mas fora de <repositório>/docker.
    f"proxy: bind {ROOT}/deploy/coolify/Caddyfile",
    f"proxy: bind {ROOT}/deploy/coolify/sites",
    # Dados do Caddy no volume do próprio projeto `edge`, não de outro projeto da VPS.
    "volume caddy_config: external",
    "volume caddy_data: external",
)
EXPECTED_DEVIATIONS: dict[str, tuple[str, ...]] = {
    "app.json": (),
    "infra.json": (),
    "gateway.json": GATEWAY_DEVIATIONS,
}
COOLIFY_NAMES = tuple(EXPECTED_DEVIATIONS)
# Cada sabotagem é a mudança no serviço e o problema que a política precisa apontar.
SABOTAGES: list[tuple[dict[str, Any], str]] = [
    ({"privileged": True}, "privileged"),
    ({"cap_add": ["SYS_ADMIN"]}, "cap_add"),
    ({"network_mode": "host"}, "network_mode=host"),
    ({"pid": "host"}, "pid=host"),
    ({"image": "caddy:2"}, "image sem digest: caddy:2"),
    ({"image": "${GS_APP_IMAGE:-ghcr.io/x/y:latest}"}, "image sem digest: ghcr.io/x/y:latest"),
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


def new_problems(document: dict[str, Any], name: str) -> list[str]:
    violations = coolify_policy_violations(rendered(document), HOST)
    return [problem for problem in violations if problem not in EXPECTED_DEVIATIONS[name]]


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


def test_todo_json_do_coolify_passa_pela_politica() -> None:
    assert sorted(path.name for path in COOLIFY_DIR.glob("*.json")) == sorted(COOLIFY_NAMES)


@pytest.mark.parametrize("name", COOLIFY_NAMES)
def test_o_compose_em_json_da_producao_respeita_a_politica_do_host(name: str) -> None:
    config = rendered(coolify_document(name))

    assert coolify_policy_violations(config, HOST) == list(EXPECTED_DEVIATIONS[name])


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

    problems = new_problems(sabotaged(document, service, changes), name)

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

    problems = new_problems(with_mount(document, service, mount), name)

    assert problems == [f"{service}: {problem}"]


@pytest.mark.parametrize("name", COOLIFY_NAMES)
def test_a_politica_alcanca_todos_os_servicos_do_json(name: str) -> None:
    document = coolify_document(name)
    services = list(document["services"])
    for service in services:
        document = sabotaged(document, service, {"privileged": True})

    problems = new_problems(document, name)

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
    config = {"services": {"app": {"build": "."}}}

    assert coolify_policy_violations(config, HOST) == ["app: image sem digest: ausente"]


def test_a_regra_de_digest_nao_alcanca_o_compose_da_vps() -> None:
    assert host_policy_violations(config_with({"image": "caddy:2"}), HOST) == []
