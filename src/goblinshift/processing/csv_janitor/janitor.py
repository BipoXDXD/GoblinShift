"""Limpeza de uma tabela pelo CSV Janitor (docs/GoblinShiftPlan.md, 1.3).

Ordem fixa: ler estrutura → normalizar colunas selecionadas → filtrar → deduplicar →
selecionar/ordenar colunas → serializar. Todo campo é texto; nada é inferido.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from goblinshift.processing.csv_janitor.limits import Limits
from goblinshift.processing.csv_janitor.params import CaseMode, FormulaGuard, Params
from goblinshift.processing.csv_janitor.reading import read_table
from goblinshift.processing.csv_janitor.results import (
    Counts,
    RejectionCode,
    TableCleaned,
    TableRejected,
    TableRejectionError,
    TableWarning,
    WarningCode,
)
from goblinshift.processing.csv_janitor.writing import serialize_record

# Começos que uma planilha pode interpretar como fórmula, inclusive as formas de largura total
# de = + - @ (U+FF1D, U+FF0B, U+FF0D, U+FF20), escritas por escape para não se confundirem.
FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r", "\n", "\uff1d", "\uff0b", "\uff0d", "\uff20")
FORMULA_GUARD_PREFIX = "\t"

_CASE: Mapping[CaseMode, Callable[[str], str]] = {
    # Operações Unicode do Python fixado, sem depender do locale do servidor.
    CaseMode.LOWER: str.lower,
    CaseMode.UPPER: str.upper,
}


@dataclass(frozen=True)
class _Record:
    position: int  # índice do registro lógico na entrada, a partir de 1
    fields: tuple[str, ...]


def clean_table(
    data: bytes, params: Params, limits: Limits | None = None
) -> TableCleaned | TableRejected:
    """Limpa uma tabela inteira ou a rejeita inteira; nunca devolve parte dela."""
    try:
        return _clean(data, params, limits or Limits())
    except TableRejectionError as rejection:
        return TableRejected(rejection.code, rejection.reason)


def _clean(data: bytes, params: Params, limits: Limits) -> TableCleaned:
    table = read_table(data, params.input_delimiter, limits)
    column = _index_columns(table.header, params)
    normalize = _normalizer(params, column)
    records = [_Record(n, normalize(fields)) for n, fields in enumerate(table.records, start=1)]
    kept = [
        record
        for record in records
        if all(f.matches(record.fields[column[f.column]]) for f in params.filters)
    ]
    survivors = _deduplicate(kept, [column[name] for name in params.deduplicate_by])
    output_header = params.output_columns or table.header
    projection = [column[name] for name in output_header]
    output = [_Record(r.position, tuple(r.fields[i] for i in projection)) for r in survivors]
    formula_cells = _count_formula_cells(output_header, output)
    # O nome da coluna também é uma célula da planilha.
    formula_header = tuple(name for name in output_header if _looks_like_formula(name))
    if params.formula_guard is FormulaGuard.TAB_PREFIX:
        output_header = tuple(map(_guard, output_header))
        output = [_Record(r.position, tuple(map(_guard, r.fields))) for r in output]
    counts = Counts(
        records_read=len(records),
        filtered_out=len(records) - len(kept),
        duplicates_removed=len(kept) - len(survivors),
        written=len(survivors),
        blank_lines_ignored=table.blank_lines_ignored,
    )
    return TableCleaned(
        content=_serialize(output_header, output, params, limits),
        counts=counts,
        formula_cells=formula_cells,
        formula_header=formula_header,
        warnings=(
            *_filter_warnings(counts),
            *_formula_warnings(formula_cells, formula_header, params.formula_guard),
        ),
    )


def _index_columns(header: tuple[str, ...], params: Params) -> Mapping[str, int]:
    """Confere todas as referências contra o cabeçalho antes de transformar qualquer registro."""
    column = {name: index for index, name in enumerate(header)}
    missing = [name for name in params.referenced_columns if name not in column]
    if missing:
        raise TableRejectionError(
            RejectionCode.CSV_STRUCTURE_INVALID, f"colunas inexistentes no cabeçalho: {missing}"
        )
    return column


def _normalizer(
    params: Params, column: Mapping[str, int]
) -> Callable[[tuple[str, ...]], tuple[str, ...]]:
    steps: dict[int, list[Callable[[str], str]]] = {}
    for name in params.trim_columns:
        # Só os espaços das pontas (qualquer espaço Unicode); os internos ficam.
        steps.setdefault(column[name], []).append(str.strip)
    for rule in params.case_rules:
        steps.setdefault(column[rule.column], []).append(_CASE[rule.mode])

    def normalize(fields: tuple[str, ...]) -> tuple[str, ...]:
        result = list(fields)
        for index, transforms in steps.items():
            for transform in transforms:
                result[index] = transform(result[index])
        return tuple(result)

    return normalize


def _deduplicate(records: list[_Record], key: Sequence[int]) -> list[_Record]:
    """Preserva a primeira ocorrência de cada tupla de valores, na ordem original."""
    if not key:
        return records
    seen: set[tuple[str, ...]] = set()
    survivors = []
    for record in records:
        values = tuple(record.fields[i] for i in key)
        if values not in seen:
            seen.add(values)
            survivors.append(record)
    return survivors


def _count_formula_cells(header: Sequence[str], records: list[_Record]) -> Mapping[str, int]:
    counts = {
        name: sum(_looks_like_formula(record.fields[i]) for record in records)
        for i, name in enumerate(header)
    }
    return {name: count for name, count in counts.items() if count}


def _looks_like_formula(value: str) -> bool:
    return value.startswith(FORMULA_PREFIXES)


def _guard(value: str) -> str:
    return FORMULA_GUARD_PREFIX + value if _looks_like_formula(value) else value


def _serialize(
    header: Sequence[str], records: list[_Record], params: Params, limits: Limits
) -> bytes:
    lines = [_encode(header, "o cabeçalho", params, limits)]
    lines += [_encode(r.fields, f"o registro {r.position}", params, limits) for r in records]
    return b"".join(lines)


def _encode(fields: Sequence[str], position: str, params: Params, limits: Limits) -> bytes:
    """Os limites valem também para a saída: caixa e prefixo podem aumentar o texto (ß → SS)."""
    for column, value in enumerate(fields, start=1):
        size = len(value.encode())
        if size > limits.field_bytes:
            raise TableRejectionError(
                RejectionCode.FIELD_LIMIT_EXCEEDED,
                f"na saída, {position}, campo {column}: {size} bytes; "
                f"o limite é {limits.field_bytes}",
            )
    line = serialize_record(fields, params.output_delimiter).encode()
    if len(line) > limits.record_bytes:
        raise TableRejectionError(
            RejectionCode.FIELD_LIMIT_EXCEEDED,
            f"na saída, {position} passa de {limits.record_bytes} bytes",
        )
    return line


def _filter_warnings(counts: Counts) -> tuple[TableWarning, ...]:
    if not counts.records_read or counts.written:
        return ()
    return (
        TableWarning(
            WarningCode.ALL_RECORDS_FILTERED,
            f"os filtros removeram os {counts.records_read} registros; a saída tem só o cabeçalho",
        ),
    )


def _formula_warnings(
    formula_cells: Mapping[str, int], formula_header: Sequence[str], guard: FormulaGuard
) -> tuple[TableWarning, ...]:
    treatment = (
        "receberam o prefixo tab dentro de aspas"
        if guard is FormulaGuard.TAB_PREFIX
        else "foram preservados sem alteração"
    )
    warnings = []
    if formula_header:
        warnings.append(
            TableWarning(
                WarningCode.FORMULA_HEADER,
                f"nomes de coluna que uma planilha pode interpretar como fórmula "
                f"({', '.join(map(repr, formula_header))}) {treatment}.",
            )
        )
    if formula_cells:
        per_column = ", ".join(f"{name!r}: {count}" for name, count in formula_cells.items())
        warnings.append(
            TableWarning(
                WarningCode.FORMULA_CELLS,
                f"valores que uma planilha pode interpretar como fórmula ({per_column}) "
                f"{treatment}. O início com '-' gera muitos falsos positivos (números "
                "negativos). Um CSV válido não é seguro para qualquer planilha.",
            )
        )
    return tuple(warnings)
