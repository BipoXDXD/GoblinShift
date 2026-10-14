"""Parâmetros do CSV Janitor (docs/GoblinShiftPlan.md, 1.3).

`Params` nasce válido: as regras que não dependem da tabela (combinações, repetições, número
de filtros) são conferidas na construção. As referências a colunas só podem ser conferidas
contra o cabeçalho de cada tabela, na limpeza.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

MAX_FILTERS = 5


class InvalidParametersError(ValueError):
    """Configuração recusada antes de ler qualquer tabela."""


class Delimiter(StrEnum):
    COMMA = ","
    SEMICOLON = ";"
    TAB = "\t"


class CaseMode(StrEnum):
    LOWER = "lower"
    UPPER = "upper"


class FilterOp(StrEnum):
    EQ = "eq"
    NE = "ne"
    CONTAINS = "contains"
    IS_EMPTY = "is_empty"


class FormulaGuard(StrEnum):
    NONE = "none"
    TAB_PREFIX = "tab_prefix"


@dataclass(frozen=True)
class CaseRule:
    column: str
    mode: CaseMode


@dataclass(frozen=True)
class Filter:
    """Predicado sobre o texto de uma coluna; `value` só não existe em `is_empty`."""

    column: str
    op: FilterOp
    value: str | None = None

    def __post_init__(self) -> None:
        if self.op is FilterOp.IS_EMPTY and self.value is not None:
            raise InvalidParametersError(f"o filtro is_empty de {self.column!r} não usa valor")
        if self.op is not FilterOp.IS_EMPTY and self.value is None:
            raise InvalidParametersError(f"o filtro {self.op} de {self.column!r} exige um valor")

    def matches(self, text: str) -> bool:
        match self.op:
            case FilterOp.EQ:
                return text == self.value
            case FilterOp.NE:
                return text != self.value
            case FilterOp.CONTAINS:
                return self.value is not None and self.value in text
            case FilterOp.IS_EMPTY:
                return text == ""


@dataclass(frozen=True)
class Params:
    input_delimiter: Delimiter = Delimiter.COMMA
    output_delimiter: Delimiter = Delimiter.COMMA
    trim_columns: tuple[str, ...] = ()
    case_rules: tuple[CaseRule, ...] = ()
    filters: tuple[Filter, ...] = ()
    deduplicate_by: tuple[str, ...] = ()
    # None preserva todas as colunas na ordem original; tupla vazia é inválida.
    output_columns: tuple[str, ...] | None = None
    formula_guard: FormulaGuard = FormulaGuard.NONE

    def __post_init__(self) -> None:
        _require_distinct("trim_columns", self.trim_columns)
        _require_distinct("case_rules", [rule.column for rule in self.case_rules])
        _require_distinct("deduplicate_by", self.deduplicate_by)
        if len(self.filters) > MAX_FILTERS:
            raise InvalidParametersError(f"no máximo {MAX_FILTERS} filtros")
        if self.output_columns is not None:
            if not self.output_columns:
                raise InvalidParametersError("output_columns vazia; omita para manter todas")
            _require_distinct("output_columns", self.output_columns)
        if "" in self.referenced_columns:
            raise InvalidParametersError("nome de coluna vazio")

    @property
    def referenced_columns(self) -> tuple[str, ...]:
        """Colunas citadas pela configuração, sem repetição, na ordem em que aparecem."""
        referenced = [
            *self.trim_columns,
            *(rule.column for rule in self.case_rules),
            *(f.column for f in self.filters),
            *self.deduplicate_by,
            *(self.output_columns or ()),
        ]
        return tuple(dict.fromkeys(referenced))


def _require_distinct(option: str, columns: Sequence[str]) -> None:
    repeated = sorted({c for c in columns if columns.count(c) > 1})
    if repeated:
        raise InvalidParametersError(f"{option} repete colunas: {repeated}")


_KNOWN_OPTIONS = frozenset(
    {
        "input_delimiter",
        "output_delimiter",
        "trim_columns",
        "case_rules",
        "filters",
        "deduplicate_by",
        "output_columns",
        "formula_guard",
    }
)


def params_from_mapping(raw: Mapping[str, object]) -> Params:
    """Converte a configuração decodificada de JSON; recusa campos desconhecidos e tipos errados."""
    unknown = sorted(set(raw) - _KNOWN_OPTIONS)
    if unknown:
        raise InvalidParametersError(f"opções desconhecidas: {unknown}")
    output_columns = raw.get("output_columns")
    return Params(
        input_delimiter=_enum(Delimiter, raw.get("input_delimiter", ","), "input_delimiter"),
        output_delimiter=_enum(Delimiter, raw.get("output_delimiter", ","), "output_delimiter"),
        trim_columns=_strings(raw.get("trim_columns", []), "trim_columns"),
        case_rules=tuple(
            _case_rule(item) for item in _objects(raw.get("case_rules", []), "case_rules")
        ),
        filters=tuple(_filter(item) for item in _objects(raw.get("filters", []), "filters")),
        deduplicate_by=_strings(raw.get("deduplicate_by", []), "deduplicate_by"),
        output_columns=None
        if output_columns is None
        else _strings(output_columns, "output_columns"),
        formula_guard=_enum(FormulaGuard, raw.get("formula_guard", "none"), "formula_guard"),
    )


def _case_rule(raw: Mapping[str, object]) -> CaseRule:
    _require_keys(raw, required={"column", "mode"}, optional=set(), option="case_rules")
    return CaseRule(
        column=_string(raw["column"], "case_rules.column"),
        mode=_enum(CaseMode, raw["mode"], "case_rules.mode"),
    )


def _filter(raw: Mapping[str, object]) -> Filter:
    _require_keys(raw, required={"column", "op"}, optional={"value"}, option="filters")
    value = raw.get("value")
    return Filter(
        column=_string(raw["column"], "filters.column"),
        op=_enum(FilterOp, raw["op"], "filters.op"),
        value=None if value is None else _string(value, "filters.value"),
    )


def _require_keys(
    raw: Mapping[str, object], *, required: set[str], optional: set[str], option: str
) -> None:
    missing = sorted(required - set(raw))
    unknown = sorted(set(raw) - required - optional)
    if missing or unknown:
        raise InvalidParametersError(f"{option}: faltando {missing}, desconhecidos {unknown}")


def _enum[E: StrEnum](kind: type[E], raw: object, option: str) -> E:
    try:
        return kind(_string(raw, option))
    except ValueError:
        allowed = [member.value for member in kind]
        raise InvalidParametersError(f"{option} deve ser um de {allowed}") from None


def _string(raw: object, option: str) -> str:
    if not isinstance(raw, str):
        raise InvalidParametersError(f"{option} deve ser texto")
    return raw


def _strings(raw: object, option: str) -> tuple[str, ...]:
    if not isinstance(raw, list):
        raise InvalidParametersError(f"{option} deve ser uma lista de textos")
    return tuple(_string(item, option) for item in raw)


def _objects(raw: object, option: str) -> list[Mapping[str, object]]:
    if not isinstance(raw, list) or not all(isinstance(item, dict) for item in raw):
        raise InvalidParametersError(f"{option} deve ser uma lista de objetos")
    return raw
