"""O plano interno do Media Mill: tudo o que o pipeline precisa, sem o FFmpeg.

`_Mill` (em `recipe.py`) roda um `MillPlan`; v1 e v2 são dois adapters finos que montam o plano a
partir dos parâmetros de cada versão. A variação entre versões é dado (formato, bitrate, trecho),
não algoritmo: por isso um plano congelado e `match` sobre os enums, sem hierarquia de classes.
Converter `Params` em `ParamsV2` não serviria: a v1 está congelada (1.6) e os `details` dela não
ganham os campos da v2.
"""

from dataclasses import dataclass

from goblinshift.processing.media_mill.params import Params
from goblinshift.processing.media_mill.params_v2 import (
    DEFAULT_BITRATE_KBPS,
    AudioFormatV2,
    ParamsV2,
)


@dataclass(frozen=True)
class MillPlan:
    extract_audio: bool
    audio_format: AudioFormatV2
    audio_bitrate_kbps: int
    audio_stream_index: int | None
    thumbnail_count: int
    trim_start_ms: int
    trim_end_ms: int | None  # None: até o fim do vídeo
    # A v1 está congelada (1.6): os `details` dela não ganham taxa de saída, bitrate nem trecho.
    v2_details: bool = False

    @property
    def trimmed(self) -> bool:
        return self.trim_start_ms > 0 or self.trim_end_ms is not None


def plan_from_v1(params: Params) -> MillPlan:
    return MillPlan(
        extract_audio=params.extract_audio,
        audio_format=AudioFormatV2(params.audio_format.value),
        audio_bitrate_kbps=DEFAULT_BITRATE_KBPS,
        audio_stream_index=params.audio_stream_index,
        thumbnail_count=params.thumbnail_count,
        trim_start_ms=0,
        trim_end_ms=None,
    )


def plan_from_v2(params: ParamsV2) -> MillPlan:
    return MillPlan(
        extract_audio=params.extract_audio,
        audio_format=params.audio_format,
        audio_bitrate_kbps=params.audio_bitrate_kbps,
        audio_stream_index=params.audio_stream_index,
        thumbnail_count=params.thumbnail_count,
        trim_start_ms=params.trim_start_ms,
        trim_end_ms=params.trim_end_ms,
        v2_details=True,
    )
