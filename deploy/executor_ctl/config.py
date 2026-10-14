"""Configuração do executor-ctl, lida de /etc/goblinshift/executor-ctl.json (dono root)."""

import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

DEFAULT_CONFIG_PATH = Path("/etc/goblinshift/executor-ctl.json")

# runc só serve para desenvolvimento e para a CI sem gVisor; em produção é runsc (ADR 004).
ALLOWED_RUNTIMES = frozenset({"runsc", "runc"})
# Caminhos entram em `--mount=type=bind,src=...`: vírgula ou `=` mudariam as opções da montagem.
_SAFE_PATH = re.compile(r"/[A-Za-z0-9._/-]+")
_GROUP_NAME = re.compile(r"[a-z_][a-z0-9_-]{0,31}")
_PATH_KEYS = ("socket", "work_area", "images_file")
_REQUIRED_KEYS = frozenset({*_PATH_KEYS, "group", "runtime"})
_OPTIONAL_KEYS = frozenset({"require_mount"})


class ConfigError(ValueError):
    """Configuração ausente ou inválida: o serviço não sobe com ela."""


@dataclass(frozen=True)
class Config:
    socket: Path
    group: str
    work_area: Path
    runtime: str
    images_file: Path
    # Sem o filesystem de tamanho fixo montado, o executor escreveria no disco raiz da VPS
    # (ADR 005); só os testes, com a área num diretório temporário, desligam a exigência.
    require_mount: bool = True


def load_config(path: Path) -> Config:
    try:
        content = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ConfigError(f"não foi possível ler {path}: {error}") from error
    if not isinstance(content, dict):
        raise ConfigError(f"{path} precisa conter um objeto JSON")
    keys = set(content)
    missing = _REQUIRED_KEYS - keys
    if missing:
        raise ConfigError(f"faltam chaves em {path}: {sorted(missing)}")
    unknown = keys - _REQUIRED_KEYS - _OPTIONAL_KEYS
    if unknown:
        raise ConfigError(f"chaves desconhecidas em {path}: {sorted(unknown)}")
    return Config(
        socket=_absolute_path(content, "socket"),
        group=_group(content["group"]),
        work_area=_absolute_path(content, "work_area"),
        runtime=_runtime(content["runtime"]),
        images_file=_absolute_path(content, "images_file"),
        require_mount=_flag(content.get("require_mount", True)),
    )


def _absolute_path(content: dict[str, object], key: str) -> Path:
    value = content[key]
    if not isinstance(value, str) or not _SAFE_PATH.fullmatch(value):
        raise ConfigError(f"{key} precisa ser um caminho absoluto só com [A-Za-z0-9._/-]")
    if ".." in PurePosixPath(value).parts:
        raise ConfigError(f"{key} não pode conter '..'")
    return Path(value)


def _group(value: object) -> str:
    if not isinstance(value, str) or not _GROUP_NAME.fullmatch(value):
        raise ConfigError("group precisa ser um nome de grupo do sistema")
    return value


def _runtime(value: object) -> str:
    if value not in ALLOWED_RUNTIMES:
        raise ConfigError(f"runtime precisa ser um de {sorted(ALLOWED_RUNTIMES)}")
    return str(value)


def _flag(value: object) -> bool:
    if not isinstance(value, bool):
        raise ConfigError("require_mount precisa ser true ou false")
    return value
