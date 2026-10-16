"""Amostra da tabela limpa para pré-visualização: 100 registros ou 64 KiB, escapada (2.2)."""

import csv
import io

import pytest

from goblinshift.processing.csv_janitor import Limits
from goblinshift.processing.csv_janitor.params import Delimiter
from goblinshift.processing.csv_janitor.preview import (
    PREVIEW_MAX_BYTES,
    PREVIEW_MAX_RECORDS,
    table_preview,
)
from goblinshift.processing.csv_janitor.writing import serialize_record


def table(records: int, *, value: str = "x", delimiter: str = ",") -> bytes:
    lines = [f"a{delimiter}b"] + [f"{value}{i}{delimiter}{i}" for i in range(records)]
    return ("\r\n".join(lines) + "\r\n").encode()


def rows(sample: bytes | None) -> list[list[str]]:
    assert sample is not None
    return list(csv.reader(io.StringIO(sample.decode(), newline="")))


def test_tabela_pequena_vira_amostra_inteira_com_virgula() -> None:
    sample = table_preview(table(2, delimiter=";"), Delimiter.SEMICOLON, Limits())

    assert rows(sample) == [["a", "b"], ["x0", "0"], ["x1", "1"]]


def test_amostra_para_em_100_registros() -> None:
    sample = table_preview(table(150), Delimiter.COMMA, Limits())

    assert PREVIEW_MAX_RECORDS == 100
    assert len(rows(sample)) == 1 + 100


def test_amostra_nunca_passa_de_64_kib() -> None:
    sample = table_preview(table(50, value="v" * 2000), Delimiter.COMMA, Limits())

    assert sample is not None
    assert PREVIEW_MAX_BYTES == 64 * 1024
    assert len(sample) <= PREVIEW_MAX_BYTES
    assert 1 < len(rows(sample)) < 51


def test_valor_que_parece_formula_sai_escapado_uma_vez() -> None:
    content = b'a,b\r\n=SOMA(1),"\t=1"\r\n'

    assert rows(table_preview(content, Delimiter.COMMA, Limits()))[1] == ["\t=SOMA(1)", "\t=1"]


def test_cabecalho_maior_que_o_teto_nao_tem_amostra() -> None:
    header = ",".join(f"c{i}" + "h" * 700 for i in range(100))

    assert table_preview((header + "\r\n").encode(), Delimiter.COMMA, Limits()) is None


@pytest.mark.parametrize(
    ("value", "escaped"),
    [
        ("+1", "\t+1"),
        ("-1", "\t-1"),
        ("@SOMA(A1)", "\t@SOMA(A1)"),
        ("=1+1", "\t=1+1"),
        ("\uff1d1", "\t\uff1d1"),
        ("\r=1", "\t\r=1"),
        ("\t=1", "\t=1"),
        (" =1", " =1"),
        ("texto", "texto"),
    ],
    ids=["mais", "menos", "arroba", "igual", "igual-largo", "cr", "ja-com-tab", "espaco", "texto"],
)
def test_escape_de_formula_por_prefixo(value: str, escaped: str) -> None:
    # O espaço inicial não está na lista de prefixos do Janitor (planilhas não avaliam " =1").
    content = serialize_record(["a"], ",").encode() + serialize_record([value], ",").encode()

    assert rows(table_preview(content, Delimiter.COMMA, Limits()))[1] == [escaped]


def test_cabecalho_que_parece_formula_sai_escapado() -> None:
    content = b"=A1,nome\r\n1,2\r\n"

    assert rows(table_preview(content, Delimiter.COMMA, Limits()))[0] == ["\t=A1", "nome"]
