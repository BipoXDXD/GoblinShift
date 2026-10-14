"""Parâmetros do image-forge v1 como modelo Pydantic estrito (docs/GoblinShiftPlan.md, 1.2).

As faixas e o padrão da cor aparecem no schema publicado; as regras moram em
`processing.image_forge.Params`, que o validador do modelo inteiro chama, para não haver duas
fontes. Dentro da versão 1, schema, defaults e enums nunca mudam (1.6).

Importa só o `__init__` do pacote da receita, que não carrega o PIL (contrato do import-linter).
"""

from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, Strict, field_validator, model_validator

from goblinshift.processing.image_forge import (
    HexColor,
    InvalidParametersError,
    OutputFormat,
    Params,
)
from goblinshift.processing.image_forge.params import (
    DEFAULT_JPEG_QUALITY,
    DEFAULT_SIDE,
    MAX_JPEG_QUALITY,
    MAX_SIDE,
    MIN_JPEG_QUALITY,
    MIN_SIDE,
    WHITE,
)

type Side = Annotated[int, Field(ge=MIN_SIDE, le=MAX_SIDE)]
# Só para o schema publicado; quem decide é `HexColor.parse`.
HEX_COLOR_PATTERN = "^#[0-9A-Fa-f]{6}$"


class ImageForgeV1Parameters(BaseModel):
    model_config = ConfigDict(
        extra="forbid", strict=True, frozen=True, title="ImageForgeV1Parameters"
    )

    max_width: Side = DEFAULT_SIDE
    max_height: Side = DEFAULT_SIDE
    # Enums chegam como o texto do JSON; o modo estrito do Pydantic só aceitaria a instância.
    output_format: Annotated[OutputFormat, Strict(False)] = OutputFormat.PRESERVE
    jpeg_quality: Annotated[int, Field(ge=MIN_JPEG_QUALITY, le=MAX_JPEG_QUALITY)] = (
        DEFAULT_JPEG_QUALITY
    )
    jpeg_background: Annotated[str, Field(pattern=HEX_COLOR_PATTERN)] = str(WHITE)
    make_thumbnails: bool = False

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

    def to_params(self) -> Params:
        return Params(
            max_width=self.max_width,
            max_height=self.max_height,
            output_format=self.output_format,
            jpeg_quality=self.jpeg_quality,
            jpeg_background=HexColor.parse(self.jpeg_background),
            make_thumbnails=self.make_thumbnails,
        )
