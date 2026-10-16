"""Rascunho de preset: nome e configuração já validados, antes de tocar o banco (1.6).

A configuração passa pelo mesmo catálogo que `POST /runs`: versão conhecida e não retirada, e
parâmetros válidos para ela. Assim um preset nunca guarda o que uma execução recusaria.

O preset guarda os parâmetros como o cliente os enviou, não os resolvidos: a execução os resolve
de novo, e os resolvidos nem sempre passam por uma segunda validação (o Detox recusa
`name_prefix` enviado com `name_mode: preserve`, e o default dele está nos resolvidos; ver
`catalog.downloads_detox_v1`). Como os defaults nunca mudam dentro de uma versão (1.6), o resultado
é o mesmo.
"""

import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass

from goblinshift.catalog import (
    CATALOG,
    FieldError,
    InvalidParametersError,
    RecipeVersion,
    RecipeVersionRetiredError,
    UnknownRecipeVersionError,
    VersionStatus,
)
from goblinshift.processing.names import has_unsafe_char

MAX_NAME_CHARS = 100


class InvalidPresetError(ValueError):
    """`VALIDATION_FAILED`, com um erro por campo a partir da raiz do corpo."""

    def __init__(self, errors: Iterable[FieldError]) -> None:
        self.errors = tuple(errors)
        super().__init__("; ".join(f"{e.location}: {e.message}" for e in self.errors))


@dataclass(frozen=True)
class PresetDraft:
    name: str
    recipe: RecipeVersion
    # Como enviados e já validados pela versão (não os resolvidos; ver o topo do módulo).
    parameters: dict[str, object]


def parse_draft(
    name: str, recipe_id: str, recipe_version: int, raw_parameters: object
) -> PresetDraft:
    """Valida tudo de uma vez e devolve todos os erros juntos; versão retirada é recusa própria."""
    errors: list[FieldError] = []
    # Aparado antes de validar e de gravar: "Foo" e "Foo " seriam o mesmo nome para quem lê a
    # lista, e a chave natural (dono, nome) precisa recusar o segundo.
    normalized_name = unicodedata.normalize("NFC", name).strip()
    name_error = _name_error(normalized_name)
    if name_error is not None:
        errors.append(FieldError("name", name_error))
    try:
        recipe = CATALOG.get_version(recipe_id, recipe_version)
    except UnknownRecipeVersionError:
        raise InvalidPresetError(
            [*errors, FieldError("recipe_version", "versão de receita desconhecida")]
        ) from None
    if recipe.status is VersionStatus.RETIRED:
        raise RecipeVersionRetiredError(f"{recipe.recipe_id} v{recipe.version}")
    try:
        recipe.resolve(raw_parameters)
    except InvalidParametersError as error:
        raise InvalidPresetError([*errors, *map(_under_parameters, error.errors)]) from None
    if errors:
        raise InvalidPresetError(errors)
    if not isinstance(raw_parameters, dict):
        raise TypeError("parâmetros válidos que não são objeto")
    return PresetDraft(normalized_name, recipe, raw_parameters)


def _under_parameters(error: FieldError) -> FieldError:
    location = f"parameters.{error.location}" if error.location else "parameters"
    return FieldError(location, error.message)


def _name_error(name: str) -> str | None:
    if not name:
        return "informe um nome"
    if len(name) > MAX_NAME_CHARS:
        return f"o nome tem no máximo {MAX_NAME_CHARS} caracteres"
    if has_unsafe_char(name):
        return "o nome não aceita caracteres de controle nem de formato invisível"
    return None
