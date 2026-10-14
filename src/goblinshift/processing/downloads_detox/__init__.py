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

__all__ = [
    "DEFAULT_NAME_PREFIX",
    "MAX_NAME_PREFIX_CHARS",
    "GroupBy",
    "InvalidParametersError",
    "NameMode",
    "Params",
    "params_from_mapping",
]
