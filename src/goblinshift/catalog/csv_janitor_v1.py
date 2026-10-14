"""Parâmetros do csv-janitor v1 como modelo Pydantic estrito (docs/GoblinShiftPlan.md, 1.3).

As regras de combinação moram em `processing.csv_janitor.Params`; os validadores daqui só as
chamam campo a campo, para o erro apontar o campo certo sem duplicar a regra. Dentro da versão 1,
schema, defaults e enums nunca mudam (1.6): qualquer mudança de comportamento é a versão 2.
"""

from collections.abc import Callable
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, Strict, field_validator, model_validator

from goblinshift.processing.csv_janitor import (
    CaseMode,
    CaseRule,
    Delimiter,
    Filter,
    FilterOp,
    FormulaGuard,
    InvalidParametersError,
    Limits,
    Params,
)
from goblinshift.processing.csv_janitor.params import MAX_FILTERS

# Uma configuração não referencia mais colunas distintas do que uma tabela pode ter (1.7).
MAX_COLUMNS = Limits().columns

_STRICT = ConfigDict(extra="forbid", strict=True, frozen=True)

type ColumnName = Annotated[str, Field(min_length=1)]
# Enums chegam como o texto do JSON; o modo estrito do Pydantic só aceitaria a instância.
type DelimiterOption = Annotated[Delimiter, Strict(False)]


class CaseRuleParameters(BaseModel):
    model_config = _STRICT

    column: ColumnName
    mode: Annotated[CaseMode, Strict(False)]

    def to_rule(self) -> CaseRule:
        return CaseRule(self.column, self.mode)


class FilterParameters(BaseModel):
    model_config = _STRICT

    column: ColumnName
    op: Annotated[FilterOp, Strict(False)]
    value: str | None = None

    @model_validator(mode="after")
    def _value_matches_operator(self) -> Self:
        _enforce(self.to_filter)
        return self

    def to_filter(self) -> Filter:
        return Filter(self.column, self.op, self.value)


class CsvJanitorV1Parameters(BaseModel):
    model_config = ConfigDict(
        extra="forbid", strict=True, frozen=True, title="CsvJanitorV1Parameters"
    )

    input_delimiter: DelimiterOption = Delimiter.COMMA
    output_delimiter: DelimiterOption = Delimiter.COMMA
    trim_columns: list[ColumnName] = Field(default_factory=list, max_length=MAX_COLUMNS)
    case_rules: list[CaseRuleParameters] = Field(default_factory=list, max_length=MAX_COLUMNS)
    filters: list[FilterParameters] = Field(default_factory=list, max_length=MAX_FILTERS)
    deduplicate_by: list[ColumnName] = Field(default_factory=list, max_length=MAX_COLUMNS)
    # Ausente (null) preserva todas as colunas na ordem original; lista vazia é inválida.
    output_columns: list[ColumnName] | None = Field(
        default=None, min_length=1, max_length=MAX_COLUMNS
    )
    formula_guard: Annotated[FormulaGuard, Strict(False)] = FormulaGuard.NONE

    @field_validator("trim_columns")
    @classmethod
    def _trim_columns_rules(cls, columns: list[str]) -> list[str]:
        _enforce(lambda: Params(trim_columns=tuple(columns)))
        return columns

    @field_validator("case_rules")
    @classmethod
    def _case_rules_rules(cls, rules: list[CaseRuleParameters]) -> list[CaseRuleParameters]:
        _enforce(lambda: Params(case_rules=tuple(rule.to_rule() for rule in rules)))
        return rules

    @field_validator("deduplicate_by")
    @classmethod
    def _deduplicate_by_rules(cls, columns: list[str]) -> list[str]:
        _enforce(lambda: Params(deduplicate_by=tuple(columns)))
        return columns

    @field_validator("output_columns")
    @classmethod
    def _output_columns_rules(cls, columns: list[str] | None) -> list[str] | None:
        _enforce(lambda: Params(output_columns=None if columns is None else tuple(columns)))
        return columns

    @model_validator(mode="after")
    def _whole_configuration_rules(self) -> Self:
        _enforce(self.to_params)
        return self

    def to_params(self) -> Params:
        return Params(
            input_delimiter=self.input_delimiter,
            output_delimiter=self.output_delimiter,
            trim_columns=tuple(self.trim_columns),
            case_rules=tuple(rule.to_rule() for rule in self.case_rules),
            filters=tuple(f.to_filter() for f in self.filters),
            deduplicate_by=tuple(self.deduplicate_by),
            output_columns=None if self.output_columns is None else tuple(self.output_columns),
            formula_guard=self.formula_guard,
        )


def _enforce(build: Callable[[], object]) -> None:
    """Traduz a recusa da biblioteca no `ValueError` que o Pydantic põe no campo validado."""
    try:
        build()
    except InvalidParametersError as error:
        raise ValueError(str(error)) from None
