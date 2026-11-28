"""Codificação de entrada explícita (docs/GoblinShiftPlan.md, 1.3): nada é adivinhado."""

import pytest

from goblinshift.processing.csv_janitor import (
    Delimiter,
    FormulaGuard,
    InputEncoding,
    Limits,
    Params,
    RejectionCode,
)
from tests.csv_janitor.support import cleaned, output, rejected


def test_windows_1252_decodifica_acentos_e_euro() -> None:
    data = "nome;preço\r\nJoão;€ 10\r\n".encode("cp1252")
    params = Params(input_delimiter=Delimiter.SEMICOLON, input_encoding=InputEncoding.WINDOWS_1252)

    assert output(data, params) == "nome,preço\r\nJoão,€ 10\r\n"


def test_latin_1_decodifica_acentos() -> None:
    data = "nome\r\nJoão\r\n".encode("latin-1")

    assert output(data, Params(input_encoding=InputEncoding.LATIN_1)) == "nome\r\nJoão\r\n"


def test_byte_indefinido_no_windows_1252_rejeita_o_item() -> None:
    rejection = rejected(b"a\r\nx\x81y\r\n", Params(input_encoding=InputEncoding.WINDOWS_1252))

    assert rejection.code is RejectionCode.UNSUPPORTED_FORMAT
    assert rejection.reason == "não é Windows-1252 válido (byte 4)"


def test_latin_1_recusa_controle_c1_e_sugere_windows_1252() -> None:
    rejection = rejected(b"a\r\n\x80\r\n", Params(input_encoding=InputEncoding.LATIN_1))

    assert rejection.code is RejectionCode.UNSUPPORTED_FORMAT
    assert "byte 3" in rejection.reason
    assert "Windows-1252" in rejection.reason


@pytest.mark.parametrize("encoding", [InputEncoding.WINDOWS_1252, InputEncoding.LATIN_1])
def test_bom_utf8_com_codificacao_de_8_bits_e_recusado(encoding: InputEncoding) -> None:
    rejection = rejected("﻿nome\r\nAna\r\n".encode(), Params(input_encoding=encoding))

    assert rejection.code is RejectionCode.UNSUPPORTED_FORMAT
    assert "BOM do UTF-8" in rejection.reason


def test_utf8_invalido_mantem_a_mensagem_da_v1() -> None:
    assert rejected(b"a\r\n\xff\r\n").reason == "não é UTF-8 válido (byte 3)"


def test_formula_em_windows_1252_e_contada_e_protegida() -> None:
    data = 'nome,obs\r\n=HYPERLINK("x"),café\r\n'.encode("cp1252")
    params = Params(
        input_encoding=InputEncoding.WINDOWS_1252, formula_guard=FormulaGuard.TAB_PREFIX
    )

    result = cleaned(data, params)

    assert result.formula_cells == {"nome": 1}
    assert result.content.decode().endswith('"\t=HYPERLINK(""x"")",café\r\n')


def test_nbsp_antes_do_igual_em_latin_1_conta_como_formula() -> None:
    result = cleaned(b"a\r\n\xa0=1+1\r\n", Params(input_encoding=InputEncoding.LATIN_1))

    assert result.formula_cells == {"a": 1}


def test_tabela_no_limite_em_windows_1252_que_cresce_em_utf8_e_rejeitada_na_saida() -> None:
    # 45 bytes crus; 125 bytes em UTF-8, acima do teto da tabela de saída.
    data = ("a\r\n" + "€" * 40 + "\r\n").encode("cp1252")
    params = Params(input_encoding=InputEncoding.WINDOWS_1252)

    rejection = rejected(data, params, Limits(table_bytes=64))

    assert rejection.code is RejectionCode.FIELD_LIMIT_EXCEEDED
    assert rejection.reason.startswith("na saída")
