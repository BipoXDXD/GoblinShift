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

from goblinshift.processing.media_mill.validation import (
    MAX_AUDIO_STREAM_INDEX as MAX_AUDIO_STREAM_INDEX,
)
from goblinshift.processing.media_mill.validation import (
    THUMBNAIL_COUNTS as THUMBNAIL_COUNTS,
)
from goblinshift.processing.media_mill.validation import (
    InvalidParametersError as InvalidParametersError,
)
from goblinshift.processing.media_mill.validation import (
    boolean,
    choice,
    integer,
    optional_integer,
    require_outputs_rules,
)
from goblinshift.processing.media_mill.validation import (
    is_stream_index as is_stream_index,
)

DEFAULT_THUMBNAIL_COUNT = 1


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
        require_outputs_rules(
            extract_audio=self.extract_audio,
            audio_stream_index=self.audio_stream_index,
            thumbnail_count=self.thumbnail_count,
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
        extract_audio=boolean(raw.get("extract_audio", True), "extract_audio"),
        audio_format=choice(
            raw.get("audio_format", AudioFormat.WAV.value), "audio_format", AudioFormat
        ),
        audio_stream_index=optional_integer(raw.get("audio_stream_index"), "audio_stream_index"),
        thumbnail_count=integer(
            raw.get("thumbnail_count", DEFAULT_THUMBNAIL_COUNT), "thumbnail_count"
        ),
    )


_KNOWN_OPTIONS = frozenset(field.name for field in fields(Params))
