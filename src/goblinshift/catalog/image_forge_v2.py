"""Parâmetros do image-forge v2 como modelo Pydantic estrito (ADR 066).

Superconjunto da v1: os mesmos campos, faixas e defaults, mais `fit`, `scale_percent`,
`webp_quality` e `webp_lossless`, e o `webp` no `output_format`. Por isso parâmetros válidos da
v1 valem na v2 com o mesmo plano, e a Bancada abre uma execução da v1 no formulário da v2. As
regras moram em `processing.image_forge.ParamsV2`, que o validador do modelo inteiro chama.
Dentro da versão 2, schema, defaults e enums nunca mudam (1.6).

Importa só o `__init__` do pacote da receita e módulos sem o PIL (contrato do import-linter).
"""

from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, Strict, field_validator, model_validator

from goblinshift.catalog.image_forge_v1 import HEX_COLOR_PATTERN, Side
from goblinshift.processing.image_forge import (
    HexColor,
    InvalidParametersError,
    OutputFormatV2,
    ParamsV2,
)
from goblinshift.processing.image_forge.geometry import FitMode
from goblinshift.processing.image_forge.params import (
    DEFAULT_JPEG_QUALITY,
    DEFAULT_SIDE,
    MAX_JPEG_QUALITY,
    MIN_JPEG_QUALITY,
    WHITE,
)
from goblinshift.processing.image_forge.params_v2 import (
    DEFAULT_SCALE_PERCENT,
    DEFAULT_WEBP_QUALITY,
    MAX_SCALE_PERCENT,
    MAX_WEBP_QUALITY,
    MIN_SCALE_PERCENT,
    MIN_WEBP_QUALITY,
)


class ImageForgeV2Parameters(BaseModel):
    model_config = ConfigDict(
        extra="forbid", strict=True, frozen=True, title="ImageForgeV2Parameters"
    )

    max_width: Side = DEFAULT_SIDE
    max_height: Side = DEFAULT_SIDE
    # Enums chegam como o texto do JSON; o modo estrito do Pydantic só aceitaria a instância.
    output_format: Annotated[OutputFormatV2, Strict(False)] = OutputFormatV2.PRESERVE
    jpeg_quality: Annotated[int, Field(ge=MIN_JPEG_QUALITY, le=MAX_JPEG_QUALITY)] = (
        DEFAULT_JPEG_QUALITY
    )
    jpeg_background: Annotated[str, Field(pattern=HEX_COLOR_PATTERN)] = str(WHITE)
    make_thumbnails: bool = False
    fit: Annotated[FitMode, Strict(False)] = FitMode.CONTAIN
    scale_percent: Annotated[int, Field(ge=MIN_SCALE_PERCENT, le=MAX_SCALE_PERCENT)] = (
        DEFAULT_SCALE_PERCENT
    )
    webp_quality: Annotated[int, Field(ge=MIN_WEBP_QUALITY, le=MAX_WEBP_QUALITY)] = (
        DEFAULT_WEBP_QUALITY
    )
    webp_lossless: bool = False

    @field_validator("jpeg_background")
    @classmethod
    def _canonical_color(cls, text: str) -> str:
        try:
            return str(HexColor.parse(text))
        except InvalidParametersError as error:
            raise ValueError(str(error)) from None

    @model_validator(mode="after")
    def _whole_configuration_rules(self) -> Self:
        try:
            self.to_params()
        except InvalidParametersError as error:
            raise ValueError(str(error)) from None
        return self

    def to_params(self) -> ParamsV2:
        return ParamsV2(
            max_width=self.max_width,
            max_height=self.max_height,
            output_format=self.output_format,
            jpeg_quality=self.jpeg_quality,
            jpeg_background=HexColor.parse(self.jpeg_background),
            make_thumbnails=self.make_thumbnails,
            fit=self.fit,
            scale_percent=self.scale_percent,
            webp_quality=self.webp_quality,
            webp_lossless=self.webp_lossless,
        )
