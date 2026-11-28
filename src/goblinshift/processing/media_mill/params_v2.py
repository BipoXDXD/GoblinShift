"""Parâmetros do Media Mill v2: áudio com perda (MP3, AAC e Opus) e corte (ADR 063).

O `AudioFormat` da v1 fica intocado (1.6: dentro de uma versão publicada, enums não mudam); a v2
tem o enum próprio, superconjunto do da v1. As listas de bitrate são as de D6 (a): todos os valores
valem em 44,1/48 kHz, mono ou estéreo, sem o encoder cortar o pedido em silêncio.

`ParamsV2` traz os campos da v1 com as mesmas regras, mais o bitrate e o trecho em milissegundos
inteiros. O bitrate fica no default com WAV/FLAC ou sem `extract_audio`: como na v1, a regra compara
com o default, porque a forma resolvida pelo catálogo sempre traz os defaults. O trecho vale para o
áudio e para as miniaturas; o fim ausente (`None`) vai até o fim do vídeo.
"""

from collections.abc import Mapping
from dataclasses import dataclass, fields
from enum import StrEnum
from types import MappingProxyType

from goblinshift.processing.media_mill.params import (
    DEFAULT_THUMBNAIL_COUNT,
    MAX_AUDIO_STREAM_INDEX,
    THUMBNAIL_COUNTS,
    is_stream_index,
)
from goblinshift.processing.media_mill.validation import (
    InvalidParametersError,
    boolean,
    choice,
    integer,
    is_not_integer,
    optional_integer,
)


class AudioFormatV2(StrEnum):
    WAV = "wav"  # PCM 16-bit little-endian, como na v1
    FLAC = "flac"  # FLAC 16-bit, como na v1
    MP3 = "mp3"  # LAME, sem ID3 nem cabeçalho Xing
    AAC = "aac"  # AAC nativo do FFmpeg, em .m4a
    OPUS = "opus"  # libopus, sempre a 48 kHz


DEFAULT_BITRATE_KBPS = 192
LOSSY = frozenset({AudioFormatV2.MP3, AudioFormatV2.AAC, AudioFormatV2.OPUS})
BITRATES_KBPS: Mapping[AudioFormatV2, tuple[int, ...]] = MappingProxyType(
    {
        AudioFormatV2.MP3: (128, 160, 192, 256, 320),
        AudioFormatV2.AAC: (96, 128, 160, 192, 256),
        AudioFormatV2.OPUS: (64, 96, 128, 160, 192),
    }
)
# O trecho cabe na duração máxima do vídeo (1.5: 300 s).
MAX_TRIM_MS = 300_000


@dataclass(frozen=True)
class ParamsV2:
    extract_audio: bool = True
    audio_format: AudioFormatV2 = AudioFormatV2.WAV
    audio_stream_index: int | None = None
    thumbnail_count: int = DEFAULT_THUMBNAIL_COUNT
    audio_bitrate_kbps: int = DEFAULT_BITRATE_KBPS
    trim_start_ms: int = 0
    trim_end_ms: int | None = None

    def __post_init__(self) -> None:
        if is_not_integer(self.thumbnail_count) or self.thumbnail_count not in THUMBNAIL_COUNTS:
            raise InvalidParametersError(f"thumbnail_count deve ser um de {THUMBNAIL_COUNTS}")
        if self.audio_stream_index is not None and not is_stream_index(self.audio_stream_index):
            raise InvalidParametersError(
                f"audio_stream_index deve estar entre 0 e {MAX_AUDIO_STREAM_INDEX}"
            )
        if not self.extract_audio and self.thumbnail_count == 0:
            raise InvalidParametersError(
                "peça ao menos uma saída: extract_audio ou thumbnail_count maior que 0"
            )
        _require_trim_ms("trim_start_ms", self.trim_start_ms, 0)
        if self.trim_end_ms is not None:
            _require_trim_ms("trim_end_ms", self.trim_end_ms, 1)
            if self.trim_end_ms <= self.trim_start_ms:
                raise InvalidParametersError("trim_end_ms deve ser maior que trim_start_ms")
        if is_not_integer(self.audio_bitrate_kbps):
            raise InvalidParametersError("audio_bitrate_kbps deve ser inteiro")
        if not self.extract_audio:
            self._require_audio_options_at_default()
        elif self.audio_format in LOSSY:
            allowed = BITRATES_KBPS[self.audio_format]
            if self.audio_bitrate_kbps not in allowed:
                raise InvalidParametersError(
                    f"audio_bitrate_kbps deve ser um de {list(allowed)} "
                    f"com audio_format {self.audio_format.value}"
                )
        elif self.audio_bitrate_kbps != DEFAULT_BITRATE_KBPS:
            raise InvalidParametersError(
                f"audio_bitrate_kbps só vale com áudio com perda; "
                f"audio_format é {self.audio_format.value}"
            )

    def _require_audio_options_at_default(self) -> None:
        changed = [
            name
            for name, default in (
                ("audio_format", AudioFormatV2.WAV),
                ("audio_stream_index", None),
                ("audio_bitrate_kbps", DEFAULT_BITRATE_KBPS),
            )
            if getattr(self, name) != default
        ]
        if changed:
            raise InvalidParametersError(
                f"{', '.join(changed)} só vale com extract_audio; extract_audio é false"
            )


def params_v2_from_mapping(raw: Mapping[str, object]) -> ParamsV2:
    """Converte a configuração decodificada de JSON; recusa campos desconhecidos e tipos errados."""
    unknown = sorted(set(raw) - _KNOWN_OPTIONS)
    if unknown:
        raise InvalidParametersError(f"opções desconhecidas: {unknown}")
    return ParamsV2(
        extract_audio=boolean(raw.get("extract_audio", True), "extract_audio"),
        audio_format=choice(
            raw.get("audio_format", AudioFormatV2.WAV.value), "audio_format", AudioFormatV2
        ),
        audio_stream_index=optional_integer(raw.get("audio_stream_index"), "audio_stream_index"),
        thumbnail_count=integer(
            raw.get("thumbnail_count", DEFAULT_THUMBNAIL_COUNT), "thumbnail_count"
        ),
        audio_bitrate_kbps=integer(
            raw.get("audio_bitrate_kbps", DEFAULT_BITRATE_KBPS), "audio_bitrate_kbps"
        ),
        trim_start_ms=integer(raw.get("trim_start_ms", 0), "trim_start_ms"),
        trim_end_ms=optional_integer(raw.get("trim_end_ms"), "trim_end_ms"),
    )


_KNOWN_OPTIONS = frozenset(field.name for field in fields(ParamsV2))


def _require_trim_ms(option: str, value: int, minimum: int) -> None:
    if is_not_integer(value):
        raise InvalidParametersError(f"{option} deve ser inteiro")
    if not minimum <= value <= MAX_TRIM_MS:
        raise InvalidParametersError(f"{option} deve estar entre {minimum} e {MAX_TRIM_MS}")
