"""Política do Compose de produção contra o host (ADR 034).

Recebe a configuração já mesclada e interpolada (`docker compose config --format json`) e lista o
que daria a um container acesso ao host ou a outro projeto da VPS compartilhada (ADR 011): modo
privilegiado, capabilities extras, namespaces do host ou de outro container, dispositivos,
confinamento desligado e montagens fora de uma allowlist explícita. A lista vazia é o único
resultado aceito. A única exceção é a dos volumes do Caddy do gateway (ADR 034).

Os JSON de `deploy/coolify/`, que são o que a produção executa, passam por essa mesma política e
ainda precisam de imagem fixada por digest, sem `build` (`coolify_policy_violations`).
"""

import posixpath
import re
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from typing import Any

HOST_ROOT = "/srv/goblinshift"
SECRETS_DIR = f"{HOST_ROOT}/secrets"
# Escrita: dados da aplicação nos filesystems de tamanho fixo e o socket do executor-ctl.
WRITABLE_BINDS = frozenset(
    {
        f"{HOST_ROOT}/storage",
        f"{HOST_ROOT}/pg/data",
        f"{HOST_ROOT}/telemetry/data",
        f"{HOST_ROOT}/exec-work",
        "/run/goblinshift",
    }
)
# Só leitura: o statfs do Collector para o alerta de disco (compose.prod.yaml).
READ_ONLY_BINDS = frozenset({f"{HOST_ROOT}/pg", f"{HOST_ROOT}/storage", f"{HOST_ROOT}/telemetry"})
# Arquivos de configuração versionados, montados só para leitura, vêm de <repositório>/docker.
REPOSITORY_CONFIG_DIR = "docker"
NAMESPACE_OPTIONS = ("pid", "network_mode", "ipc", "userns_mode", "uts", "cgroup")
UNCONFINED_SECURITY_OPTS = frozenset(
    {"seccomp=unconfined", "apparmor=unconfined", "label=disable", "systempaths=unconfined"}
)
HARMLESS_MOUNT_TYPES = frozenset({"volume", "tmpfs"})
PINNED_IMAGE = re.compile(r"[^@]+@sha256:[0-9a-f]{64}")
# Exceção do ADR 034: `external` protege os certificados do gateway. Vale só para estas chaves, com
# estes nomes exatos, neste projeto e neste serviço; nome reservado em qualquer outro lugar reprova.
PROTECTED_VOLUMES = {"caddy_data": "edge_caddy_data", "caddy_config": "edge_caddy_config"}
PROTECTED_VOLUME_PROJECT = "edge"
PROTECTED_VOLUME_SERVICE = "proxy"


@dataclass(frozen=True)
class HostPaths:
    """Onde o repositório está no momento da verificação (o Compose resolve `./` a partir dele)."""

    project_dir: str

    def is_repository_config(self, path: str) -> bool:
        return is_under(path, posixpath.join(self.project_dir, REPOSITORY_CONFIG_DIR))


def host_policy_violations(config: Mapping[str, Any], host: HostPaths) -> list[str]:
    violations: list[str] = []
    project = config.get("name")
    for name, service in (config.get("services") or {}).items():
        violations += [f"{name}: {problem}" for problem in service_option_problems(service)]
        violations += [f"{name}: {problem}" for problem in service_mount_problems(service, host)]
        if project == PROTECTED_VOLUME_PROJECT and name != PROTECTED_VOLUME_SERVICE:
            violations += [f"{name}: {problem}" for problem in protected_mount_problems(service)]
    violations += list(top_level_volume_problems(config.get("volumes") or {}, project))
    violations += list(file_source_problems("secret", config.get("secrets") or {}, is_secret_file))
    violations += list(
        file_source_problems("config", config.get("configs") or {}, host.is_repository_config)
    )
    return violations


def coolify_policy_violations(config: Mapping[str, Any], host: HostPaths) -> list[str]:
    """A política do host sobre os JSON de `deploy/coolify/`, com a imagem fixada por digest."""
    return [*host_policy_violations(config, host), *image_source_problems(config)]


def image_source_problems(config: Mapping[str, Any]) -> Iterator[str]:
    for name, service in (config.get("services") or {}).items():
        image = service.get("image")
        if image is None or PINNED_IMAGE.fullmatch(image) is None:
            yield f"{name}: image sem digest: {image or 'ausente'}"
        if "build" in service:
            yield f"{name}: build"


def service_option_problems(service: Mapping[str, Any]) -> Iterator[str]:
    if service.get("privileged"):
        yield "privileged"
    if service.get("cap_add"):
        yield "cap_add"
    for option in NAMESPACE_OPTIONS:
        value = str(service.get(option) or "")
        if value == "host" or value.startswith("container:"):
            yield f"{option}={value}"
    if service.get("devices"):
        yield "devices"
    for security_opt in service.get("security_opt") or []:
        if security_opt.replace(":", "=", 1) in UNCONFINED_SECURITY_OPTS:
            yield f"security_opt {security_opt}"
    if service.get("volumes_from"):
        yield "volumes_from"
    if service.get("cgroup_parent"):
        yield "cgroup_parent"
    # Compose repassa ao container o acesso à API do Docker.
    if service.get("use_api_socket"):
        yield "use_api_socket"


def service_mount_problems(service: Mapping[str, Any], host: HostPaths) -> Iterator[str]:
    for mount in service.get("volumes") or []:
        mount_type = mount.get("type")
        if mount_type == "bind":
            if not is_allowed_bind(mount["source"], bool(mount.get("read_only")), host):
                yield f"bind {mount['source']}"
        elif mount_type not in HARMLESS_MOUNT_TYPES:
            yield f"mount {mount_type}"


def protected_mount_problems(service: Mapping[str, Any]) -> Iterator[str]:
    for mount in service.get("volumes") or []:
        if mount.get("type") == "volume" and mount.get("source") in PROTECTED_VOLUMES:
            yield f"monta {mount['source']}"


def is_allowed_bind(source: str, read_only: bool, host: HostPaths) -> bool:
    if not is_normalized(source):
        return False
    if source in WRITABLE_BINDS:
        return True
    return read_only and (source in READ_ONLY_BINDS or host.is_repository_config(source))


def top_level_volume_problems(volumes: Mapping[str, Any], project: str | None) -> Iterator[str]:
    for name, volume in volumes.items():
        volume = volume or {}
        protected = is_protected_volume(name, volume, project)
        # Volume de outro projeto da VPS (ADR 011).
        if volume.get("external") and not protected:
            yield f"volume {name}: external"
        # `name` monta um volume que já existe mesmo sem `external`: vale o nome, não a chave.
        real_name = volume.get("name")
        if real_name is not None and not real_name.startswith(f"{project}_"):
            yield f"volume {name}: nome {real_name} fora do projeto {project}"
        elif real_name in PROTECTED_VOLUMES.values() and not protected:
            yield f"volume {name}: nome reservado {real_name}"
        # O driver `local` com `o: bind` e `device` monta qualquer caminho do host.
        if volume.get("driver_opts"):
            yield f"volume {name}: driver_opts"


def is_protected_volume(name: str, volume: Mapping[str, Any], project: str | None) -> bool:
    return (
        project == PROTECTED_VOLUME_PROJECT
        and name in PROTECTED_VOLUMES
        and volume.get("external") is True
        and volume.get("name") == PROTECTED_VOLUMES.get(name)
    )


def file_source_problems(
    kind: str, entries: Mapping[str, Any], is_allowed: Callable[[str], bool]
) -> Iterator[str]:
    for name, entry in entries.items():
        path = (entry or {}).get("file")
        if path is not None and not is_allowed(path):
            yield f"{kind} {name}: {path}"


def is_secret_file(path: str) -> bool:
    return is_under(path, SECRETS_DIR)


def is_under(path: str, directory: str) -> bool:
    return is_normalized(path) and path.startswith(f"{directory}/")


def is_normalized(path: str) -> bool:
    return posixpath.isabs(path) and posixpath.normpath(path) == path
