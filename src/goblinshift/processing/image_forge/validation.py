"""Validação dos parâmetros crus comum à v1 e à v2, sem o PIL.

O erro mora aqui para os dois módulos de parâmetros importarem sem ciclo; `params` o reexporta.
"""


class InvalidParametersError(ValueError):
    """Configuração recusada antes de ler qualquer imagem."""


def require_range(option: str, value: int, minimum: int, maximum: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidParametersError(f"{option} deve ser inteiro")
    if not minimum <= value <= maximum:
        raise InvalidParametersError(f"{option} deve estar entre {minimum} e {maximum}")


def integer(raw: object, option: str) -> int:
    # bool é subclasse de int em Python; `true` do JSON não é um tamanho.
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise InvalidParametersError(f"{option} deve ser inteiro")
    return raw


def boolean(raw: object, option: str) -> bool:
    if not isinstance(raw, bool):
        raise InvalidParametersError(f"{option} deve ser booleano")
    return raw


def string(raw: object, option: str) -> str:
    if not isinstance(raw, str):
        raise InvalidParametersError(f"{option} deve ser texto")
    return raw
