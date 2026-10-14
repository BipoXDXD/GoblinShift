"""Propriedades do CSV Janitor (docs/GoblinShiftPlan.md, fatia 2 em 3.9).

O oráculo da leitura é o `csv.reader` da biblioteca padrão, independente do serializador próprio.
"""

import csv
import io

from hypothesis import given
from hypothesis import strategies as st

from goblinshift.processing.csv_janitor import (
    CaseMode,
    CaseRule,
    Delimiter,
    Filter,
    FilterOp,
    Params,
)
from goblinshift.processing.csv_janitor.writing import serialize_record
from tests.csv_janitor.support import cleaned

# Os caracteres que importam para a estrutura e para as regras, mais texto comum e multibyte.
FIELD_ALPHABET = st.sampled_from(list(',;\t"\r\n =-+@0123 aAbBéß'))
fields = st.text(FIELD_ALPHABET, max_size=8)
column_names = st.text(st.sampled_from(list("abcxyz")), min_size=1, max_size=3)
delimiters = st.sampled_from(list(Delimiter))


@st.composite
def tables(draw: st.DrawFn) -> tuple[tuple[str, ...], list[tuple[str, ...]]]:
    header = tuple(draw(st.lists(column_names, min_size=1, max_size=4, unique=True)))
    records = draw(st.lists(st.tuples(*[fields] * len(header)), max_size=12))
    return header, records


def to_csv(header: tuple[str, ...], records: list[tuple[str, ...]], delimiter: str) -> bytes:
    lines = [
        serialize_record(header, delimiter),
        *(serialize_record(r, delimiter) for r in records),
    ]
    return "".join(lines).encode()


def parse(content: bytes, delimiter: str = ",") -> list[list[str]]:
    reader = csv.reader(io.StringIO(content.decode(), newline=""), delimiter=delimiter, strict=True)
    return list(reader)


@given(tables(), delimiters, delimiters)
def test_ida_e_volta_sem_opcoes_preserva_cada_campo(
    table: tuple[tuple[str, ...], list[tuple[str, ...]]], source: Delimiter, target: Delimiter
) -> None:
    header, records = table
    params = Params(input_delimiter=source, output_delimiter=target)

    result = cleaned(to_csv(header, records, source), params)

    assert parse(result.content, target) == [list(header), *map(list, records)]
    assert result.counts.records_read == len(records)


@st.composite
def configurations(draw: st.DrawFn, header: tuple[str, ...], values: list[str]) -> Params:
    column = st.sampled_from(header)
    value = st.sampled_from(values) if values else fields
    predicate = st.builds(
        Filter, column, st.sampled_from([FilterOp.EQ, FilterOp.NE, FilterOp.CONTAINS]), value
    ) | st.builds(Filter, column, st.just(FilterOp.IS_EMPTY))
    case_columns = draw(st.lists(column, unique=True, max_size=2))
    return Params(
        trim_columns=tuple(draw(st.lists(column, unique=True, max_size=2))),
        case_rules=tuple(CaseRule(c, draw(st.sampled_from(list(CaseMode)))) for c in case_columns),
        filters=tuple(draw(st.lists(predicate, max_size=2))),
        deduplicate_by=tuple(draw(st.lists(column, unique=True, max_size=2))),
    )


@given(st.data(), tables())
def test_contagem_sempre_fecha(
    data: st.DataObject, table: tuple[tuple[str, ...], list[tuple[str, ...]]]
) -> None:
    header, records = table
    params = data.draw(configurations(header, [v for r in records for v in r]))

    counts = cleaned(to_csv(header, records, ","), params).counts

    assert counts.records_read == len(records)
    assert counts.records_read == counts.filtered_out + counts.duplicates_removed + counts.written
    assert len(parse(cleaned(to_csv(header, records, ","), params).content)) == counts.written + 1


@given(st.data(), tables())
def test_deduplicar_de_novo_nao_remove_mais_nada(
    data: st.DataObject, table: tuple[tuple[str, ...], list[tuple[str, ...]]]
) -> None:
    header, records = table
    params = data.draw(configurations(header, [v for r in records for v in r]))

    once = cleaned(to_csv(header, records, ","), params)
    twice = cleaned(once.content, params)

    assert twice.counts.duplicates_removed == 0
    assert twice.content == once.content


@given(
    st.lists(st.from_regex(r"0[0-9]{1,18}", fullmatch=True), min_size=1, max_size=10),
    st.sampled_from(list(CaseMode)),
)
def test_codigos_com_zeros_a_esquerda_sao_preservados(codes: list[str], mode: CaseMode) -> None:
    params = Params(trim_columns=("id",), case_rules=(CaseRule("id", mode),))
    table = "id\r\n" + "".join(f"{code}\r\n" for code in codes)

    assert [row[0] for row in parse(cleaned(table, params).content)[1:]] == codes
