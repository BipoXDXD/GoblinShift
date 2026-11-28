"""Parâmetros do media-mill v2 como modelo Pydantic estrito (ADR 063).

Superconjunto da v1: os mesmos campos e defaults, mais o bitrate do áudio com perda e o trecho em
milissegundos inteiros. As regras moram em `processing.media_mill.ParamsV2`, que o validador do
modelo inteiro chama, para não haver duas fontes. O bitrate aceito por formato também sai no
schema, como `allOf` de `if`/`then` do JSON Schema padrão: é a fonte que o front lê.

Importa só o `__init__` do pacote da receita, que não chama o FFmpeg.
"""

from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, Strict, field_validator, model_validator

from goblinshift.processing.media_mill import (
    BITRATES_KBPS,
    MAX_AUDIO_STREAM_INDEX,
    MAX_TRIM_MS,
    AudioFormatV2,
    InvalidParametersError,
    ParamsV2,
)

type ThumbnailCount = Literal[0, 1, 3]
# A união das listas de D6; a lista de cada formato vem no `allOf` do schema.
type BitrateKbps = Literal[64, 96, 128, 160, 192, 256, 320]


def _bitrate_by_format(schema: dict[str, Any]) -> None:
    schema["allOf"] = [
        {
            "if": {
                "properties": {"audio_format": {"const": audio_format.value}},
                "required": ["audio_format"],
            },
            "then": {"properties": {"audio_bitrate_kbps": {"enum": list(bitrates)}}},
        }
        for audio_format, bitrates in BITRATES_KBPS.items()
    ]


class MediaMillV2Parameters(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        frozen=True,
        title="MediaMillV2Parameters",
        json_schema_extra=_bitrate_by_format,
    )

    extract_audio: bool = True
    # Enums chegam como o texto do JSON; o modo estrito do Pydantic só aceitaria a instância.
    audio_format: Annotated[AudioFormatV2, Strict(False)] = AudioFormatV2.WAV
    audio_stream_index: Annotated[int, Field(ge=0, le=MAX_AUDIO_STREAM_INDEX)] | None = None
    thumbnail_count: ThumbnailCount = 1
    audio_bitrate_kbps: BitrateKbps = 192  # DEFAULT_BITRATE_KBPS; o Literal pede o valor literal
    trim_start_ms: Annotated[int, Field(ge=0, le=MAX_TRIM_MS)] = 0
    trim_end_ms: Annotated[int, Field(ge=1, le=MAX_TRIM_MS)] | None = None

    @field_validator("thumbnail_count", "audio_bitrate_kbps", mode="before")
    @classmethod
    def _boolean_is_not_a_number(cls, value: object) -> object:
        # O Literal compara por igualdade, e `True == 1` em Python; o JSON `true` não é número.
        if isinstance(value, bool):
            raise ValueError("deve ser inteiro")
        return value

    @model_validator(mode="after")
    def _whole_configuration_rules(self) -> Self:
        try:
            self.to_params()
        except InvalidParametersError as error:
            raise ValueError(str(error)) from None
        return self

    def to_params(self) -> ParamsV2:
        return ParamsV2(
            extract_audio=self.extract_audio,
            audio_format=self.audio_format,
            audio_stream_index=self.audio_stream_index,
            thumbnail_count=self.thumbnail_count,
            audio_bitrate_kbps=self.audio_bitrate_kbps,
            trim_start_ms=self.trim_start_ms,
            trim_end_ms=self.trim_end_ms,
        )
