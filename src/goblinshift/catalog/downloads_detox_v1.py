"""Parâmetros do downloads-detox v1 como modelo Pydantic estrito (docs/GoblinShiftPlan.md, 1.4).

As regras de `name_prefix` moram em `processing.downloads_detox.Params`; o validador daqui só a
chama, para o erro apontar o campo sem duplicar a regra. Dentro da versão 1, schema, defaults e
enums nunca mudam (1.6).

A recusa de `name_prefix` enviado com `name_mode: preserve` (A13) mora aqui, porque só o modelo
sabe o que o cliente enviou de fato (`model_fields_set`); o default continua nos parâmetros
resolvidos. Por isso parâmetros já resolvidos não devem ser validados de novo por este modelo:
quem os guarda (presets, por exemplo) guarda o corpo enviado.
"""

from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, Strict, field_validator, model_validator

from goblinshift.processing.downloads_detox import (
    DEFAULT_NAME_PREFIX,
    MAX_NAME_PREFIX_CHARS,
    GroupBy,
    InvalidParametersError,
    NameMode,
    Params,
)


class DownloadsDetoxV1Parameters(BaseModel):
    model_config = ConfigDict(
        extra="forbid", strict=True, frozen=True, title="DownloadsDetoxV1Parameters"
    )

    deduplicate: bool = True
    # Enums chegam como o texto do JSON; o modo estrito do Pydantic só aceitaria a instância.
    group_by: Annotated[GroupBy, Strict(False)] = GroupBy.KIND
    name_mode: Annotated[NameMode, Strict(False)] = NameMode.PRESERVE
    name_prefix: Annotated[str, Field(min_length=1, max_length=MAX_NAME_PREFIX_CHARS)] = (
        DEFAULT_NAME_PREFIX
    )

    @field_validator("name_prefix")
    @classmethod
    def _name_prefix_rules(cls, prefix: str) -> str:
        try:
            Params(name_prefix=prefix)
        except InvalidParametersError as error:
            raise ValueError(str(error)) from None
        return prefix

    @model_validator(mode="after")
    def _prefix_only_with_indexed(self) -> Self:
        if "name_prefix" in self.model_fields_set and self.name_mode is NameMode.PRESERVE:
            raise ValueError("name_prefix só se aplica a name_mode indexed")
        return self
