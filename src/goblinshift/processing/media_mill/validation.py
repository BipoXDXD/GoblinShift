"""Validação dos parâmetros crus comum à v1 e à v2, sem o FFmpeg.

O erro mora aqui para os dois módulos de parâmetros importarem sem ciclo; `params` o reexporta.
"""

from enum import StrEnum

THUMBNAIL_COUNTS = (0, 1, 3)
MAX_AUDIO_STREAM_INDEX = 15


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


def is_stream_index(value: int) -> bool:
    return not is_not_integer(value) and 0 <= value <= MAX_AUDIO_STREAM_INDEX


def require_outputs_rules(
    *, extract_audio: bool, audio_stream_index: int | None, thumbnail_count: int
) -> None:
    """As regras de miniaturas, faixa e saída pedida, iguais na v1 e na v2."""
    if is_not_integer(thumbnail_count) or thumbnail_count not in THUMBNAIL_COUNTS:
        raise InvalidParametersError(f"thumbnail_count deve ser um de {THUMBNAIL_COUNTS}")
    if audio_stream_index is not None and not is_stream_index(audio_stream_index):
        raise InvalidParametersError(
            f"audio_stream_index deve estar entre 0 e {MAX_AUDIO_STREAM_INDEX}"
        )
    if not extract_audio and thumbnail_count == 0:
        raise InvalidParametersError(
            "peça ao menos uma saída: extract_audio ou thumbnail_count maior que 0"
        )
