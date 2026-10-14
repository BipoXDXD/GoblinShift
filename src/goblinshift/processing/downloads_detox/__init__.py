"""Receita downloads-detox (docs/GoblinShiftPlan.md, 1.4), implementada pela frente C da fatia 5.

Só biblioteca padrão: o Detox classifica e copia bytes, nunca decodifica conteúdo.
"""

from goblinshift.processing.downloads_detox.params import (
    DEFAULT_NAME_PREFIX,
    MAX_NAME_PREFIX_CHARS,
    GroupBy,
    InvalidParametersError,
    NameMode,
    Params,
    params_from_mapping,
)
from goblinshift.processing.downloads_detox.recipe import (
    MAX_INPUTS,
    MAX_TOTAL_INPUT_BYTES,
    RECIPE_ID,
    VERSION,
    DownloadsDetoxV1,
)

__all__ = [
    "DEFAULT_NAME_PREFIX",
    "MAX_INPUTS",
    "MAX_NAME_PREFIX_CHARS",
    "MAX_TOTAL_INPUT_BYTES",
    "RECIPE_ID",
    "VERSION",
    "DownloadsDetoxV1",
    "GroupBy",
    "InvalidParametersError",
    "NameMode",
    "Params",
    "params_from_mapping",
]
