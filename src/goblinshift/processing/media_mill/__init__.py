"""Receita media-mill (docs/GoblinShiftPlan.md, 1.5; ADR 036).

Este `__init__` não pode importar módulos que chamam o FFmpeg: o catálogo, na API, importa os
parâmetros e limites daqui. A receita fica em `media_mill.recipe`, que o registro do executor
importa diretamente.
"""

from goblinshift.processing.media_mill.limits import (
    MAX_INPUT_BYTES,
    MAX_INPUTS,
    MAX_TOTAL_INPUT_BYTES,
    Limits,
)
from goblinshift.processing.media_mill.params import (
    MAX_AUDIO_STREAM_INDEX,
    THUMBNAIL_COUNTS,
    AudioFormat,
    InvalidParametersError,
    Params,
    params_from_mapping,
)
from goblinshift.processing.media_mill.params_v2 import (
    BITRATES_KBPS,
    DEFAULT_BITRATE_KBPS,
    MAX_TRIM_MS,
    AudioFormatV2,
    ParamsV2,
    params_v2_from_mapping,
)
from goblinshift.processing.media_mill.version import RECIPE_ID, VERSION, VERSION_1, VERSION_2

__all__ = [
    "BITRATES_KBPS",
    "DEFAULT_BITRATE_KBPS",
    "MAX_AUDIO_STREAM_INDEX",
    "MAX_INPUTS",
    "MAX_INPUT_BYTES",
    "MAX_TOTAL_INPUT_BYTES",
    "MAX_TRIM_MS",
    "RECIPE_ID",
    "THUMBNAIL_COUNTS",
    "VERSION",
    "VERSION_1",
    "VERSION_2",
    "AudioFormat",
    "AudioFormatV2",
    "InvalidParametersError",
    "Limits",
    "Params",
    "ParamsV2",
    "params_from_mapping",
    "params_v2_from_mapping",
]
