"""Versões de receita, estado, limites efetivos e resolução de parâmetros (1.6 e 1.7).

Montagem do contrato HTTP (fica para a API): o corpo de `POST /runs` e o de presets é um
`allOf` com os campos comuns (`input_upload_ids`, ou `name` nos presets) e um `oneOf` com o
`variant_schema()` de cada versão não retirada, e leva `unevaluatedProperties: false` nesse nível,
porque só ali o JSON Schema 2020-12 enxerga as propriedades avaliadas pelas duas partes. O
`discriminator.propertyName` é `recipe_id`; como duas versões da mesma receita compartilham esse
valor, o `mapping` aponta cada `recipe_id` para um `oneOf` das versões dela, distinguidas pela
`const` de `recipe_version`. O catálogo HTTP não copia schemas: referencia a variante por
`(recipe_id, recipe_version)`.
"""

import copy
import dataclasses
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ValidationError

from goblinshift.catalog.csv_janitor_v1 import CsvJanitorV1Parameters
from goblinshift.processing.csv_janitor import Limits
from goblinshift.processing.csv_janitor import recipe as csv_janitor

# O executor real chega na fatia 4 com imagem fixada por digest; até lá, o subprocesso local
# roda o código do próprio repositório, e a Run registra este valor no lugar do digest.
LOCAL_EXECUTOR_DIGEST = "local-dev"


class VersionStatus(StrEnum):
    CURRENT = "current"
    DEPRECATED = "deprecated"
    RETIRED = "retired"


class UnknownRecipeVersionError(LookupError):
    pass


class RecipeVersionRetiredError(Exception):
    """`RECIPE_VERSION_RETIRED`: o histórico continua legível, mas nada novo usa a versão."""


@dataclass(frozen=True)
class FieldError:
    location: str  # caminho com pontos, como `filters.0.value`; vazio é o objeto inteiro
    message: str


class InvalidParametersError(ValueError):
    """`VALIDATION_FAILED`, com um erro por campo."""

    def __init__(self, errors: Iterable[FieldError]) -> None:
        self.errors = tuple(errors)
        super().__init__("; ".join(f"{e.location or '<raiz>'}: {e.message}" for e in self.errors))


@dataclass(frozen=True)
class RecipeVersion:
    recipe_id: str
    version: int
    status: VersionStatus
    parameters_model: type[BaseModel]
    limits: Mapping[str, int]
    executor_digest: str

    def resolve(self, raw_params: object) -> dict[str, object]:
        """Parâmetros validados com os defaults da versão, em forma canônica.

        Chaves ordenadas em todos os níveis e listas na ordem recebida: é a forma que entra no
        hash de idempotência (1.6) e no `job.json` do executor.
        """
        try:
            model = self.parameters_model.model_validate(raw_params)
        except ValidationError as error:
            raise InvalidParametersError(_field_errors(error)) from None
        canonical = _sorted_keys(model.model_dump(mode="json"))
        if not isinstance(canonical, dict):
            raise TypeError("modelo de parâmetros não serializou como objeto")
        return canonical

    def parameters_schema(self) -> dict[str, Any]:
        """JSON Schema dos parâmetros, autocontido (sem `$ref`), para embutir em outro schema."""
        return _inline_refs(self.parameters_model.model_json_schema())

    def variant_schema(self) -> dict[str, Any]:
        return {
            "title": f"{self.recipe_id} v{self.version}",
            "type": "object",
            "properties": {
                "recipe_id": {"const": self.recipe_id},
                "recipe_version": {"const": self.version},
                "parameters": self.parameters_schema(),
            },
            "required": ["recipe_id", "recipe_version", "parameters"],
        }


class Catalog:
    def __init__(self, versions: Iterable[RecipeVersion]) -> None:
        self._versions = {(v.recipe_id, v.version): v for v in versions}

    def list_versions(self) -> tuple[RecipeVersion, ...]:
        return tuple(self._versions.values())

    def get_version(self, recipe_id: str, version: int) -> RecipeVersion:
        """Inclui versões retiradas, para o histórico continuar legível."""
        try:
            return self._versions[recipe_id, version]
        except KeyError:
            raise UnknownRecipeVersionError(f"{recipe_id} v{version}") from None

    def resolve(self, recipe_id: str, version: int, raw_params: object) -> dict[str, object]:
        recipe_version = self.get_version(recipe_id, version)
        if recipe_version.status is VersionStatus.RETIRED:
            raise RecipeVersionRetiredError(f"{recipe_id} v{version}")
        return recipe_version.resolve(raw_params)


CSV_JANITOR_V1 = RecipeVersion(
    recipe_id=csv_janitor.RECIPE_ID,
    version=csv_janitor.VERSION,
    status=VersionStatus.CURRENT,
    parameters_model=CsvJanitorV1Parameters,
    limits={"max_inputs": csv_janitor.MAX_TABLES, **dataclasses.asdict(Limits())},
    executor_digest=LOCAL_EXECUTOR_DIGEST,
)

CATALOG = Catalog([CSV_JANITOR_V1])


def _field_errors(error: ValidationError) -> list[FieldError]:
    return [
        FieldError(".".join(str(part) for part in detail["loc"]), detail["msg"])
        for detail in error.errors()
    ]


def _sorted_keys(value: object) -> object:
    if isinstance(value, dict):
        return {key: _sorted_keys(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_sorted_keys(item) for item in value]
    return value


def _inline_refs(schema: dict[str, Any]) -> dict[str, Any]:
    """Troca cada `{"$ref": "#/$defs/X"}` pela definição, para o schema poder ser embutido."""
    definitions = schema.pop("$defs", {})

    def inline(node: object) -> object:
        if isinstance(node, dict):
            if "$ref" in node:
                name = node["$ref"].removeprefix("#/$defs/")
                siblings = {k: v for k, v in node.items() if k != "$ref"}
                return inline({**copy.deepcopy(definitions[name]), **siblings})
            return {key: inline(item) for key, item in node.items()}
        if isinstance(node, list):
            return [inline(item) for item in node]
        return node

    inlined = inline(schema)
    if not isinstance(inlined, dict):
        raise TypeError("schema de parâmetros não é objeto")
    return inlined
