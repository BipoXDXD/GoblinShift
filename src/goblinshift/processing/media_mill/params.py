"""Parâmetros do Media Mill (docs/GoblinShiftPlan.md, 1.5; ADR 036).

`Params` nasce válido, inclusive na combinação: pelo menos uma saída pedida, e as opções de áudio
fora do default só valem com `extract_audio`. Como no Image Forge, a regra compara com o default, e
não com "o cliente enviou o campo", porque a forma resolvida pelo catálogo sempre traz os defaults.

`audio_stream_index` conta só as faixas de áudio do arquivo (0 é a primeira faixa de áudio, o
`0:a:0` do FFmpeg), não o índice global do stream. Ausente (`None`), vale a primeira faixa, e o
relatório registra o índice efetivo.
"""

from collections.abc import Mapping
from dataclasses import dataclass, fields
from enum import StrEnum

THUMBNAIL_COUNTS = (0, 1, 3)
DEFAULT_THUMBNAIL_COUNT = 1
MAX_AUDIO_STREAM_INDEX = 15


class InvalidParametersError(ValueError):
    """Configuração recusada antes de ler qualquer vídeo."""


class AudioFormat(StrEnum):
    WAV = "wav"  # PCM 16-bit little-endian
    FLAC = "flac"  # FLAC 16-bit


@dataclass(frozen=True)
class Params:
    extract_audio: bool = True
    audio_format: AudioFormat = AudioFormat.WAV
    audio_stream_index: int | None = None
    thumbnail_count: int = DEFAULT_THUMBNAIL_COUNT

    def __post_init__(self) -> None:
        if _is_not_integer(self.thumbnail_count) or self.thumbnail_count not in THUMBNAIL_COUNTS:
            raise InvalidParametersError(f"thumbnail_count deve ser um de {THUMBNAIL_COUNTS}")
        if self.audio_stream_index is not None and not _is_stream_index(self.audio_stream_index):
            raise InvalidParametersError(
                f"audio_stream_index deve estar entre 0 e {MAX_AUDIO_STREAM_INDEX}"
            )
        if not self.extract_audio and self.thumbnail_count == 0:
            raise InvalidParametersError(
                "peça ao menos uma saída: extract_audio ou thumbnail_count maior que 0"
            )
        if not self.extract_audio:
            self._require_audio_options_at_default()

    def _require_audio_options_at_default(self) -> None:
        changed = [
            name
            for name, default in (
                ("audio_format", AudioFormat.WAV),
                ("audio_stream_index", None),
            )
            if getattr(self, name) != default
        ]
        if changed:
            raise InvalidParametersError(
                f"{', '.join(changed)} só vale com extract_audio; extract_audio é false"
            )


def params_from_mapping(raw: Mapping[str, object]) -> Params:
    """Converte a configuração decodificada de JSON; recusa campos desconhecidos e tipos errados."""
    unknown = sorted(set(raw) - _KNOWN_OPTIONS)
    if unknown:
        raise InvalidParametersError(f"opções desconhecidas: {unknown}")
    return Params(
        extract_audio=_boolean(raw.get("extract_audio", True), "extract_audio"),
        audio_format=_audio_format(raw.get("audio_format", AudioFormat.WAV.value)),
        audio_stream_index=_optional_integer(raw.get("audio_stream_index"), "audio_stream_index"),
        thumbnail_count=_integer(
            raw.get("thumbnail_count", DEFAULT_THUMBNAIL_COUNT), "thumbnail_count"
        ),
    )


_KNOWN_OPTIONS = frozenset(field.name for field in fields(Params))


def _is_not_integer(value: object) -> bool:
    # bool é subclasse de int em Python; `true` do JSON não é uma quantidade.
    return isinstance(value, bool) or not isinstance(value, int)


def _is_stream_index(value: int) -> bool:
    return not _is_not_integer(value) and 0 <= value <= MAX_AUDIO_STREAM_INDEX


def _audio_format(raw: object) -> AudioFormat:
    if not isinstance(raw, str):
        raise InvalidParametersError("audio_format deve ser texto")
    try:
        return AudioFormat(raw)
    except ValueError:
        allowed = [member.value for member in AudioFormat]
        raise InvalidParametersError(f"audio_format deve ser um de {allowed}") from None


def _integer(raw: object, option: str) -> int:
    if isinstance(raw, int) and not isinstance(raw, bool):
        return raw
    raise InvalidParametersError(f"{option} deve ser inteiro")


def _optional_integer(raw: object, option: str) -> int | None:
    return None if raw is None else _integer(raw, option)


def _boolean(raw: object, option: str) -> bool:
    if not isinstance(raw, bool):
        raise InvalidParametersError(f"{option} deve ser booleano")
    return raw
