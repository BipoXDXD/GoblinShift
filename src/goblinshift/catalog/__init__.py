"""Receitas e versões disponíveis, schemas e defaults (supporting).

Ver docs/GoblinShiftPlan.md, seções 1.6 e 3.2.
"""

from goblinshift.catalog.versions import (
    CATALOG,
    LOCAL_EXECUTOR_DIGEST,
    Catalog,
    FieldError,
    InvalidParametersError,
    RecipeVersion,
    RecipeVersionRetiredError,
    UnknownRecipeVersionError,
    VersionStatus,
)

__all__ = [
    "CATALOG",
    "LOCAL_EXECUTOR_DIGEST",
    "Catalog",
    "FieldError",
    "InvalidParametersError",
    "RecipeVersion",
    "RecipeVersionRetiredError",
    "UnknownRecipeVersionError",
    "VersionStatus",
]
