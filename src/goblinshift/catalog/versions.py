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
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ValidationError

from goblinshift.catalog.csv_janitor_v1 import CsvJanitorV1Parameters
from goblinshift.catalog.downloads_detox_v1 import DownloadsDetoxV1Parameters
from goblinshift.catalog.image_forge_v1 import ImageForgeV1Parameters
from goblinshift.catalog.image_forge_v2 import ImageForgeV2Parameters
from goblinshift.catalog.media_mill_v1 import MediaMillV1Parameters
from goblinshift.catalog.pdf_press_v1 import PdfPressV1Parameters, upload_references
from goblinshift.catalog.references import UploadReference, no_upload_references
from goblinshift.processing import downloads_detox, image_forge, media_mill, pdf_press
from goblinshift.processing.csv_janitor import Limits
from goblinshift.processing.csv_janitor import recipe as csv_janitor

MIB = 1024 * 1024
# 1.7: "arquivo comum, 32 MiB". Toda versão declara `max_input_bytes` e a aceitação o aplica a cada
# entrada pelo tamanho guardado, nunca pelo nome: o envio dá 256 MiB a um nome terminado em .mp4,
# e só a versão que declara esse teto (Media Mill) aceita a entrada maior (ADR 036). O teste do
# catálogo confere que o valor é o mesmo `MAX_FILE_BYTES` de `files.limits`.
COMMON_MAX_INPUT_BYTES = 32 * MIB


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
    # Uma parte por nível, como `("filters", "0", "value")`; vazio é o objeto inteiro. Partes,
    # não texto com pontos: uma chave do cliente pode conter ponto.
    location: tuple[str, ...]
    message: str
    # Tipo do erro de validação (`extra_forbidden`, `enum`...); regra da própria receita é
    # `value_error`.
    code: str = "value_error"

    def __str__(self) -> str:
        return f"{'.'.join(self.location) or '<raiz>'}: {self.message}"


class InvalidParametersError(ValueError):
    """`VALIDATION_FAILED`, com um erro por campo."""

    def __init__(self, errors: Iterable[FieldError]) -> None:
        self.errors = tuple(errors)
        super().__init__("; ".join(map(str, self.errors)))


def _field_errors(error: ValidationError) -> list[FieldError]:
    return [
        FieldError(tuple(str(part) for part in detail["loc"]), detail["msg"], detail["type"])
        for detail in error.errors()
    ]


def _sorted_keys(value: object) -> object:
    if isinstance(value, dict):
        return {key: _sorted_keys(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_sorted_keys(item) for item in value]
    return value


# Ids do catálogo e chaves de ajuste pronto (`csv-janitor`, `web-1600`); a API usa o mesmo formato
# no caminho e nos filtros.
SLUG_PATTERN = r"^[a-z0-9]+(?:-[a-z0-9]+)*$"


@dataclass(frozen=True)
class SystemPreset:
    """Ajuste pronto de uma versão (D2): dado do produto, só leitura, fora de `/presets`.

    `parameters` é parcial; o resto vem dos defaults da versão ao resolver.
    """

    key: str
    name: str
    parameters: Mapping[str, object]

    def __post_init__(self) -> None:
        if re.fullmatch(SLUG_PATTERN, self.key) is None:
            raise ValueError(f"chave de ajuste pronto fora do formato: {self.key!r}")


@dataclass(frozen=True)
class RecipeVersion:
    recipe_id: str
    version: int
    status: VersionStatus
    parameters_model: type[BaseModel]
    limits: Mapping[str, int]
    # Uploads citados nos parâmetros já resolvidos; a aceitação exige que estejam entre as
    # entradas da execução (1.5: a seleção refere-se só aos uploads da execução).
    upload_references: Callable[[Mapping[str, object]], tuple[UploadReference, ...]] = field(
        default=no_upload_references
    )
    # Data de desligamento (header `Sunset`, RFC 8594): obrigatória em `deprecated`, que continua
    # aceita por pelo menos 90 dias (1.6); a retirada pode guardar a data em que saiu.
    sunset_at: datetime | None = None
    # Ajustes prontos publicados em `GET /catalog`; validados pelo modelo da versão no import.
    system_presets: tuple[SystemPreset, ...] = ()

    def __post_init__(self) -> None:
        if self.sunset_at is not None and self.sunset_at.tzinfo is None:
            raise ValueError("sunset_at precisa de fuso horário")
        if self.status is VersionStatus.DEPRECATED and self.sunset_at is None:
            raise ValueError("versão deprecated exige sunset_at")
        if self.status is VersionStatus.CURRENT and self.sunset_at is not None:
            raise ValueError("versão current não tem sunset_at")
        self._check_system_presets()

    def _check_system_presets(self) -> None:
        keys = [preset.key for preset in self.system_presets]
        if len(set(keys)) != len(keys):
            raise ValueError("chave de ajuste pronto repetida")
        names = [preset.name for preset in self.system_presets]
        if len(set(names)) != len(names):
            raise ValueError("nome de ajuste pronto repetido")
        # `InvalidParametersError` sobe como está: o import do catálogo falha com o erro por campo.
        for preset in self.system_presets:
            self.resolve(preset.parameters)

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


def frozen_view(version: RecipeVersion) -> dict[str, object]:
    """O que não muda dentro de uma versão publicada (1.6): schema e defaults.

    É o conteúdo dos snapshots de `tests/catalog/congeladas/`. `status`, `limits` e `sunset_at`
    ficam de fora: mudam sem versão nova (os limites de 1.7 são fixados na Run, na aceitação).
    """
    return {"variant_schema": version.variant_schema(), "defaults": version.resolve({})}


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
    limits={
        "max_inputs": csv_janitor.MAX_TABLES,
        "max_input_bytes": COMMON_MAX_INPUT_BYTES,
        **dataclasses.asdict(Limits()),
    },
)

_IMAGE_LIMITS = image_forge.Limits()

# Merge da Bancada da v2 (PR C de paridade-image-forge): a v1 vira `deprecated` (D1) e é aceita
# até o primeiro dia do mês depois de 90 dias desta data (1.6). A retirada fica num PR posterior.
IMAGE_FORGE_V2_RELEASED_ON = date(2026, 11, 28)

IMAGE_FORGE_V1 = RecipeVersion(
    recipe_id=image_forge.RECIPE_ID,
    version=image_forge.VERSION,
    status=VersionStatus.DEPRECATED,
    sunset_at=datetime(2027, 3, 1, tzinfo=UTC),
    parameters_model=ImageForgeV1Parameters,
    limits={
        "max_inputs": image_forge.MAX_INPUTS,
        "max_input_bytes": COMMON_MAX_INPUT_BYTES,
        "max_pixels": _IMAGE_LIMITS.max_pixels,
        "max_axis_px": _IMAGE_LIMITS.max_axis,
        "thumbnail_box_px": _IMAGE_LIMITS.thumbnail_box,
    },
)

# Ao lado da v1, com os mesmos limites (ADR 066). A Bancada só mostra a v2 e abre nela os
# parâmetros da v1 (superconjunto); a v1 segue aceita pela API até o `sunset_at`.
IMAGE_FORGE_V2 = RecipeVersion(
    recipe_id=image_forge.RECIPE_ID,
    version=image_forge.VERSION_2,
    status=VersionStatus.CURRENT,
    parameters_model=ImageForgeV2Parameters,
    limits=IMAGE_FORGE_V1.limits,
    system_presets=(
        SystemPreset(
            "web-1600",
            "Web 1600",
            {
                "fit": "contain",
                "max_width": 1600,
                "max_height": 1600,
                "output_format": "webp",
                "webp_quality": 80,
            },
        ),
        SystemPreset(
            "email-1024",
            "E-mail 1024",
            {
                "fit": "contain",
                "max_width": 1024,
                "max_height": 1024,
                "output_format": "jpeg",
                "jpeg_quality": 80,
            },
        ),
        SystemPreset(
            "redes-1080",
            "Redes 1080",
            {
                "fit": "cover",
                "max_width": 1080,
                "max_height": 1080,
                "output_format": "jpeg",
                "jpeg_quality": 85,
            },
        ),
    ),
)

# O teto do lote fica abaixo dos 480 MiB de conteúdo de saída (1.7): o Detox copia cada entrada
# aceita. A aceitação o impõe (`max_total_input_bytes` → 422) antes de reservar o teto de saída.
DOWNLOADS_DETOX_V1 = RecipeVersion(
    recipe_id=downloads_detox.RECIPE_ID,
    version=downloads_detox.VERSION,
    status=VersionStatus.CURRENT,
    parameters_model=DownloadsDetoxV1Parameters,
    limits={
        "max_inputs": downloads_detox.MAX_INPUTS,
        "max_input_bytes": COMMON_MAX_INPUT_BYTES,
        "max_total_input_bytes": downloads_detox.MAX_TOTAL_INPUT_BYTES,
    },
)

_MEDIA_LIMITS = media_mill.Limits()

# `max_input_bytes` sobe o teto por arquivo de 32 MiB para os 256 MiB de vídeo (1.7, ADR 036).
MEDIA_MILL_V1 = RecipeVersion(
    recipe_id=media_mill.RECIPE_ID,
    version=media_mill.VERSION,
    status=VersionStatus.CURRENT,
    parameters_model=MediaMillV1Parameters,
    limits={
        "max_inputs": media_mill.MAX_INPUTS,
        "max_input_bytes": media_mill.MAX_INPUT_BYTES,
        "max_total_input_bytes": media_mill.MAX_TOTAL_INPUT_BYTES,
        "max_duration_s": _MEDIA_LIMITS.max_duration_s,
        "max_long_side_px": _MEDIA_LIMITS.max_long_side_px,
        "max_short_side_px": _MEDIA_LIMITS.max_short_side_px,
        "max_fps": _MEDIA_LIMITS.max_fps,
        "max_audio_sample_rate_hz": _MEDIA_LIMITS.max_sample_rate_hz,
        "max_audio_channels": _MEDIA_LIMITS.max_channels,
        "thumbnail_max_side_px": _MEDIA_LIMITS.thumbnail_max_side_px,
    },
)

# ADR 035. O teto do lote é imposto na aceitação, como no Detox; páginas e documentos só se
# conhecem depois de abrir os PDFs, e o executor os impõe.
PDF_PRESS_V1 = RecipeVersion(
    recipe_id=pdf_press.RECIPE_ID,
    version=pdf_press.VERSION,
    status=VersionStatus.CURRENT,
    parameters_model=PdfPressV1Parameters,
    limits={
        "max_inputs": pdf_press.MAX_INPUTS,
        # PDF é arquivo comum para o envio (32 MiB); o lote soma até 256 MiB.
        "max_input_bytes": COMMON_MAX_INPUT_BYTES,
        "max_total_input_bytes": pdf_press.MAX_TOTAL_INPUT_BYTES,
        "max_input_pages": pdf_press.MAX_INPUT_PAGES,
        "max_output_pages": pdf_press.MAX_OUTPUT_PAGES,
        "max_output_documents": pdf_press.MAX_OUTPUT_DOCUMENTS,
    },
    upload_references=upload_references,
)

CATALOG = Catalog(
    [
        CSV_JANITOR_V1,
        IMAGE_FORGE_V1,
        IMAGE_FORGE_V2,
        DOWNLOADS_DETOX_V1,
        PDF_PRESS_V1,
        MEDIA_MILL_V1,
    ]
)


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
