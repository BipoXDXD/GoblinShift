"""Receita image-forge (docs/GoblinShiftPlan.md, 1.2), implementada pela frente B da fatia 5.

Este `__init__` não pode importar o PIL nem módulos que o importam: o catálogo, na API, importa
os parâmetros daqui (contrato "Só o executor carrega o PIL" do import-linter). A receita fica em
`image_forge.recipe`, que o registro do executor importa diretamente.
"""

from goblinshift.processing.image_forge.limits import MAX_INPUTS, Limits
from goblinshift.processing.image_forge.params import (
    HexColor,
    InvalidParametersError,
    OutputFormat,
    Params,
    params_from_mapping,
)
from goblinshift.processing.image_forge.params_v2 import (
    OutputFormatV2,
    ParamsV2,
    params_v2_from_mapping,
)
from goblinshift.processing.image_forge.version import RECIPE_ID, VERSION, VERSION_1, VERSION_2

__all__ = [
    "MAX_INPUTS",
    "RECIPE_ID",
    "VERSION",
    "VERSION_1",
    "VERSION_2",
    "HexColor",
    "InvalidParametersError",
    "Limits",
    "OutputFormat",
    "OutputFormatV2",
    "Params",
    "ParamsV2",
    "params_from_mapping",
    "params_v2_from_mapping",
]
