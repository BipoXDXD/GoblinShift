"""Registro de receitas: `(recipe_id, recipe_version)` → implementação.

Um `dict` simples, sem hierarquia de classes (docs/GoblinShiftPlan.md, 3.2). Cada entrada sabe
converter os parâmetros já resolvidos pelo catálogo (JSON canônico) no modelo da própria versão
antes de rodá-la.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Protocol

from goblinshift.processing.csv_janitor import Params, params_from_mapping
from goblinshift.processing.csv_janitor import recipe as csv_janitor
from goblinshift.processing.recipe import InputFile, OutputDir, Progress, Recipe, Report

type RecipeKey = tuple[str, int]


class RegisteredRecipe(Protocol):
    def execute(
        self,
        raw_params: Mapping[str, object],
        inputs: Sequence[InputFile],
        out: OutputDir,
        progress: Progress,
    ) -> Report: ...


@dataclass(frozen=True)
class _Bound[P]:
    parse_params: Callable[[Mapping[str, object]], P]
    recipe: Recipe[P]

    def execute(
        self,
        raw_params: Mapping[str, object],
        inputs: Sequence[InputFile],
        out: OutputDir,
        progress: Progress,
    ) -> Report:
        return self.recipe.run(self.parse_params(raw_params), inputs, out, progress)


_CSV_JANITOR_V1: _Bound[Params] = _Bound(params_from_mapping, csv_janitor.CsvJanitorV1())

RECIPES: Mapping[RecipeKey, RegisteredRecipe] = MappingProxyType(
    {(csv_janitor.RECIPE_ID, csv_janitor.VERSION): _CSV_JANITOR_V1}
)
