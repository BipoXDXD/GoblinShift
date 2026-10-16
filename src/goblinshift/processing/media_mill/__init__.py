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
from goblinshift.processing.media_mill.version import RECIPE_ID, VERSION

__all__ = [
    "MAX_AUDIO_STREAM_INDEX",
    "MAX_INPUTS",
    "MAX_INPUT_BYTES",
    "MAX_TOTAL_INPUT_BYTES",
    "RECIPE_ID",
    "THUMBNAIL_COUNTS",
    "VERSION",
    "AudioFormat",
    "InvalidParametersError",
    "Limits",
    "Params",
    "params_from_mapping",
]
