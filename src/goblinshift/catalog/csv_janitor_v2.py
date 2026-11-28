"""Parâmetros do csv-janitor v2 como modelo Pydantic estrito (docs/GoblinShiftPlan.md, 1.3).

A v2 é a v1 mais três opções: a codificação declarada da entrada, o BOM do UTF-8 na saída e as
colunas com espaços internos colapsados. Herda o modelo da v1 só para reaproveitar os
validadores; o schema e os defaults de cada versão ficam congelados em
`tests/catalog/congeladas/` (ADR 067).
"""

from dataclasses import replace
from typing import Annotated

from pydantic import ConfigDict, Field, Strict, field_validator

from goblinshift.catalog.csv_janitor_v1 import (
    MAX_COLUMNS,
    ColumnName,
    CsvJanitorV1Parameters,
    check_library_rules,
)
from goblinshift.processing.csv_janitor import InputEncoding, Params


class CsvJanitorV2Parameters(CsvJanitorV1Parameters):
    model_config = ConfigDict(
        extra="forbid", strict=True, frozen=True, title="CsvJanitorV2Parameters"
    )

    input_encoding: Annotated[InputEncoding, Strict(False)] = InputEncoding.UTF_8
    output_bom: bool = False
    collapse_whitespace_columns: list[ColumnName] = Field(
        default_factory=list, max_length=MAX_COLUMNS
    )

    @field_validator("collapse_whitespace_columns")
    @classmethod
    def _collapse_whitespace_columns_rules(cls, columns: list[str]) -> list[str]:
        check_library_rules(lambda: Params(collapse_whitespace_columns=tuple(columns)))
        return columns

    def to_params(self) -> Params:
        return replace(
            super().to_params(),
            input_encoding=self.input_encoding,
            output_bom=self.output_bom,
            collapse_whitespace_columns=tuple(self.collapse_whitespace_columns),
        )
