"""Parâmetros do media-mill v1 como modelo Pydantic estrito (docs/GoblinShiftPlan.md, 1.5).

As faixas e os enums aparecem no schema publicado; as regras moram em
`processing.media_mill.Params`, que o validador do modelo inteiro chama, para não haver duas
fontes. Dentro da versão 1, schema, defaults e enums nunca mudam (1.6).

Importa só o `__init__` do pacote da receita, que não chama o FFmpeg.
"""

from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, Strict, field_validator, model_validator

from goblinshift.processing.media_mill import (
    MAX_AUDIO_STREAM_INDEX,
    AudioFormat,
    InvalidParametersError,
    Params,
)

type ThumbnailCount = Literal[0, 1, 3]


class MediaMillV1Parameters(BaseModel):
    model_config = ConfigDict(
        extra="forbid", strict=True, frozen=True, title="MediaMillV1Parameters"
    )

    extract_audio: bool = True
    # Enums chegam como o texto do JSON; o modo estrito do Pydantic só aceitaria a instância.
    audio_format: Annotated[AudioFormat, Strict(False)] = AudioFormat.WAV
    audio_stream_index: Annotated[int, Field(ge=0, le=MAX_AUDIO_STREAM_INDEX)] | None = None
    thumbnail_count: ThumbnailCount = 1

    @field_validator("thumbnail_count", mode="before")
    @classmethod
    def _boolean_is_not_a_count(cls, value: object) -> object:
        # O Literal compara por igualdade, e `True == 1` em Python; o JSON `true` não é quantidade.
        if isinstance(value, bool):
            raise ValueError("thumbnail_count deve ser inteiro")
        return value

    @model_validator(mode="after")
    def _whole_configuration_rules(self) -> Self:
        try:
            self.to_params()
        except InvalidParametersError as error:
            raise ValueError(str(error)) from None
        return self

    def to_params(self) -> Params:
        return Params(
            extract_audio=self.extract_audio,
            audio_format=self.audio_format,
            audio_stream_index=self.audio_stream_index,
            thumbnail_count=self.thumbnail_count,
        )
