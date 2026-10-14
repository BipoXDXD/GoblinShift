"""Parâmetros do Downloads Detox (docs/GoblinShiftPlan.md, 1.4).

`Params` nasce válido. A recusa de `name_prefix` explícito com `name_mode: preserve`
(ambiguidade A13) só cabe ao catálogo, que sabe o que o cliente enviou: aqui chegam os
parâmetros já resolvidos, com o default de `name_prefix` sempre presente.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from goblinshift.processing.names import has_unsafe_char

MAX_NAME_PREFIX_CHARS = 40
DEFAULT_NAME_PREFIX = "file"
_SEPARATORS = ("/", "\\")


class InvalidParametersError(ValueError):
    """Configuração recusada antes de ler qualquer arquivo."""


class GroupBy(StrEnum):
    KIND = "kind"
    NONE = "none"


class NameMode(StrEnum):
    PRESERVE = "preserve"
    INDEXED = "indexed"


@dataclass(frozen=True)
class Params:
    deduplicate: bool = True
    group_by: GroupBy = GroupBy.KIND
    name_mode: NameMode = NameMode.PRESERVE
    # Usado só em `indexed`; vira o início de um componente de caminho (`file-001.png`).
    name_prefix: str = DEFAULT_NAME_PREFIX

    def __post_init__(self) -> None:
        _validate_name_prefix(self.name_prefix)


def _validate_name_prefix(prefix: str) -> None:
    if not prefix:
        raise InvalidParametersError("name_prefix vazio")
    if len(prefix) > MAX_NAME_PREFIX_CHARS:
        raise InvalidParametersError(f"name_prefix passa de {MAX_NAME_PREFIX_CHARS} caracteres")
    if any(separator in prefix for separator in _SEPARATORS):
        raise InvalidParametersError("name_prefix não pode ter separador de caminho")
    if has_unsafe_char(prefix):
        raise InvalidParametersError(
            "name_prefix não pode ter caractere de controle nem de formato invisível"
        )
    if prefix.startswith("."):
        # Geraria arquivo oculto (A18).
        raise InvalidParametersError("name_prefix não pode começar com ponto")


_KNOWN_OPTIONS = frozenset({"deduplicate", "group_by", "name_mode", "name_prefix"})


def params_from_mapping(raw: Mapping[str, object]) -> Params:
    """Converte a configuração decodificada de JSON; recusa campos desconhecidos e tipos errados."""
    unknown = sorted(set(raw) - _KNOWN_OPTIONS)
    if unknown:
        raise InvalidParametersError(f"opções desconhecidas: {unknown}")
    return Params(
        deduplicate=_boolean(raw.get("deduplicate", True), "deduplicate"),
        group_by=_enum(GroupBy, raw.get("group_by", GroupBy.KIND.value), "group_by"),
        name_mode=_enum(NameMode, raw.get("name_mode", NameMode.PRESERVE.value), "name_mode"),
        name_prefix=_string(raw.get("name_prefix", DEFAULT_NAME_PREFIX), "name_prefix"),
    )


def _boolean(raw: object, option: str) -> bool:
    if not isinstance(raw, bool):
        raise InvalidParametersError(f"{option} deve ser booleano")
    return raw


def _enum[E: StrEnum](kind: type[E], raw: object, option: str) -> E:
    try:
        return kind(_string(raw, option))
    except ValueError:
        allowed = [member.value for member in kind]
        raise InvalidParametersError(f"{option} deve ser um de {allowed}") from None


def _string(raw: object, option: str) -> str:
    if not isinstance(raw, str):
        raise InvalidParametersError(f"{option} deve ser texto")
    return raw
