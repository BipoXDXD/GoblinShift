"""Registro de receitas: `(recipe_id, recipe_version)` → implementação.

Um `dict` simples, sem hierarquia de classes (docs/GoblinShiftPlan.md, 3.2). Cada entrada sabe
converter os parâmetros já resolvidos pelo catálogo (JSON canônico) no modelo da própria versão
antes de rodá-la.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Protocol

from goblinshift.processing import csv_janitor, downloads_detox, image_forge, pdf_press
from goblinshift.processing.csv_janitor import recipe as csv_janitor_recipe
from goblinshift.processing.downloads_detox import DownloadsDetoxV1
from goblinshift.processing.image_forge.recipe import ImageForgeV1
from goblinshift.processing.pdf_press.recipe import PdfPressV1
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


_CSV_JANITOR_V1: _Bound[csv_janitor.Params] = _Bound(
    csv_janitor.params_from_mapping, csv_janitor_recipe.CsvJanitorV1()
)
_IMAGE_FORGE_V1: _Bound[image_forge.Params] = _Bound(
    image_forge.params_from_mapping, ImageForgeV1()
)
_DOWNLOADS_DETOX_V1: _Bound[downloads_detox.Params] = _Bound(
    downloads_detox.params_from_mapping, DownloadsDetoxV1()
)

_PDF_PRESS_V1: _Bound[pdf_press.Params] = _Bound(pdf_press.params_from_mapping, PdfPressV1())

RECIPES: Mapping[RecipeKey, RegisteredRecipe] = MappingProxyType(
    {
        (csv_janitor_recipe.RECIPE_ID, csv_janitor_recipe.VERSION): _CSV_JANITOR_V1,
        (image_forge.RECIPE_ID, image_forge.VERSION): _IMAGE_FORGE_V1,
        (downloads_detox.RECIPE_ID, downloads_detox.VERSION): _DOWNLOADS_DETOX_V1,
        (pdf_press.RECIPE_ID, pdf_press.VERSION): _PDF_PRESS_V1,
    }
)
