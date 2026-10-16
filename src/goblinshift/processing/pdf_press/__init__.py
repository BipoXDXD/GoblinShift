"""Receita pdf-press (docs/GoblinShiftPlan.md, 1.5; ADR 035).

Este `__init__` não pode importar o pikepdf nem módulos que o importam: o catálogo, na API,
importa os parâmetros daqui (contrato "Só o executor carrega o pikepdf" do import-linter). A
receita fica em `pdf_press.recipe`, que o registro do executor importa diretamente.
"""

from goblinshift.processing.pdf_press.limits import (
    MAX_INPUT_PAGES,
    MAX_INPUTS,
    MAX_OBJECTS_PER_DOCUMENT,
    MAX_OUTPUT_DOCUMENTS,
    MAX_OUTPUT_PAGES,
    MAX_TOTAL_INPUT_BYTES,
    RECIPE_ID,
    VERSION,
    Limits,
)
from goblinshift.processing.pdf_press.params import (
    DEFAULT_PAGES_PER_DOCUMENT,
    InvalidParametersError,
    Operation,
    PageSelection,
    Params,
    params_from_mapping,
)

__all__ = [
    "DEFAULT_PAGES_PER_DOCUMENT",
    "MAX_INPUTS",
    "MAX_INPUT_PAGES",
    "MAX_OBJECTS_PER_DOCUMENT",
    "MAX_OUTPUT_DOCUMENTS",
    "MAX_OUTPUT_PAGES",
    "MAX_TOTAL_INPUT_BYTES",
    "RECIPE_ID",
    "VERSION",
    "InvalidParametersError",
    "Limits",
    "Operation",
    "PageSelection",
    "Params",
    "params_from_mapping",
]
