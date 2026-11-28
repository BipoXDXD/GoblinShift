"""Validação dos parâmetros crus comum à v1 e à v2, sem o FFmpeg.

O erro mora aqui para os dois módulos de parâmetros importarem sem ciclo; `params` o reexporta.
"""

from enum import StrEnum


class InvalidParametersError(ValueError):
    """Configuração recusada antes de ler qualquer vídeo."""


def is_not_integer(value: object) -> bool:
    # bool é subclasse de int em Python; `true` do JSON não é uma quantidade.
    return isinstance(value, bool) or not isinstance(value, int)


def integer(raw: object, option: str) -> int:
    if isinstance(raw, int) and not isinstance(raw, bool):
        return raw
    raise InvalidParametersError(f"{option} deve ser inteiro")


def optional_integer(raw: object, option: str) -> int | None:
    return None if raw is None else integer(raw, option)


def boolean(raw: object, option: str) -> bool:
    if not isinstance(raw, bool):
        raise InvalidParametersError(f"{option} deve ser booleano")
    return raw


def choice[E: StrEnum](raw: object, option: str, enum: type[E]) -> E:
    if not isinstance(raw, str):
        raise InvalidParametersError(f"{option} deve ser texto")
    try:
        return enum(raw)
    except ValueError:
        allowed = [member.value for member in enum]
        raise InvalidParametersError(f"{option} deve ser um de {allowed}") from None
