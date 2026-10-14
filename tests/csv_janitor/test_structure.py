"""Especificação da leitura da estrutura (docs/GoblinShiftPlan.md, 1.3 "Estrutura" e "Entrada")."""

import pytest

from goblinshift.processing.csv_janitor import (
    CaseMode,
    CaseRule,
    Delimiter,
    Filter,
    FilterOp,
    Params,
    RejectionCode,
)
from tests.csv_janitor.support import cleaned, output, rejected


def test_bom_da_entrada_e_ignorado_e_a_saida_sai_sem_bom() -> None:
    assert output(b"\xef\xbb\xbfa,b\r\n1,2\r\n") == "a,b\r\n1,2\r\n"


@pytest.mark.parametrize(
    ("delimiter", "table"),
    [
        (Delimiter.COMMA, "a,b\r\n1,2\r\n"),
        (Delimiter.SEMICOLON, "a;b\r\n1;2\r\n"),
        (Delimiter.TAB, "a\tb\r\n1\t2\r\n"),
    ],
)
def test_le_com_o_delimitador_configurado(delimiter: Delimiter, table: str) -> None:
    assert output(table, Params(input_delimiter=delimiter)) == "a,b\r\n1,2\r\n"


def test_delimitador_diferente_do_configurado_nao_e_adivinhado() -> None:
    result = cleaned("a;b\r\n1;2\r\n")

    assert result.content == b"a;b\r\n1;2\r\n"  # uma coluna chamada "a;b"


def test_delimitador_dentro_de_aspas_fica_no_campo() -> None:
    assert output('a,b\r\n"1,5",2\r\n') == 'a,b\r\n"1,5",2\r\n'


def test_campo_com_quebra_de_linha_e_um_registro_so() -> None:
    result = cleaned('a,b\r\n"linha 1\r\nlinha 2",2\r\n')

    assert result.counts.records_read == 1
    assert result.content == b'a,b\r\n"linha 1\r\nlinha 2",2\r\n'


@pytest.mark.parametrize("table", ["a\nx\ny\n", "a\rx\ry\r", "a\r\nx\r\ny"])
def test_aceita_terminadores_lf_cr_e_ultimo_registro_sem_terminador(table: str) -> None:
    assert output(table) == "a\r\nx\r\ny\r\n"


def test_aspas_no_meio_de_campo_sem_aspas_sao_texto_literal() -> None:
    assert output('a\r\nab"c\r\n') == 'a\r\n"ab""c"\r\n'


def test_texto_depois_de_aspas_fechadas_rejeita_a_tabela() -> None:
    rejection = rejected('a\r\n1\r\n"ab"c\r\n')

    assert rejection.code is RejectionCode.CSV_STRUCTURE_INVALID
    assert "registro 2" in rejection.reason


def test_aspas_que_nao_fecham_ate_o_fim_rejeitam_a_tabela() -> None:
    rejection = rejected('a,b\r\n1,"aberto\r\n2,3\r\n')

    assert rejection.code is RejectionCode.CSV_STRUCTURE_INVALID
    assert "registro 1" in rejection.reason


@pytest.mark.parametrize(("record", "fields"), [("1", 1), ("1,2,3", 3)])
def test_numero_irregular_de_campos_rejeita_a_tabela(record: str, fields: int) -> None:
    rejection = rejected(f"a,b\r\n1,2\r\n{record}\r\n")

    assert rejection.code is RejectionCode.CSV_STRUCTURE_INVALID
    assert f"registro 2 tem {fields} campo(s)" in rejection.reason


def test_posicao_da_rejeicao_e_o_registro_logico_nao_a_linha_fisica() -> None:
    # O primeiro registro ocupa as linhas físicas 2 e 3; o problema está na linha 4.
    rejection = rejected('a,b\r\n"x\r\ny",1\r\n2\r\n')

    assert "registro 2" in rejection.reason


def test_linhas_vazias_sao_ignoradas_e_contadas_a_parte() -> None:
    result = cleaned("\r\na,b\r\n\r\n1,2\r\n\r\n\r\n3,4\r\n")

    assert result.counts.records_read == 2
    assert result.counts.blank_lines_ignored == 4
    assert result.content == b"a,b\r\n1,2\r\n3,4\r\n"


def test_registro_com_campos_vazios_continua_sendo_registro() -> None:
    result = cleaned("a,b\r\n,\r\n")

    assert result.counts.records_read == 1
    assert result.content == b"a,b\r\n,\r\n"


def test_em_tabela_de_uma_coluna_linha_vazia_conta_como_registro() -> None:
    result = cleaned("a\r\nx\r\n\r\ny\r\n")

    assert result.counts.records_read == 3
    assert result.counts.blank_lines_ignored == 0
    assert result.content == b'a\r\nx\r\n""\r\ny\r\n'


@pytest.mark.parametrize(
    ("table", "reason"),
    [
        ("", "não tem cabeçalho"),
        ("\r\n\r\n", "não tem cabeçalho"),
        ("a,,c\r\n", "coluna sem nome (coluna 2)"),
        ("a,b,a\r\n", "repete colunas: ['a']"),
    ],
)
def test_cabecalho_ausente_vazio_ou_repetido_rejeita_sem_renomear(table: str, reason: str) -> None:
    rejection = rejected(table)

    assert rejection.code is RejectionCode.CSV_STRUCTURE_INVALID
    assert reason in rejection.reason


def test_tabela_so_com_cabecalho_e_valida() -> None:
    result = cleaned("a,b\r\n")

    assert result.content == b"a,b\r\n"
    assert result.counts.records_read == 0
    assert result.warnings == ()


def test_arquivo_que_nao_e_utf8_e_rejeitado() -> None:
    rejection = rejected("cidade\r\nSão Paulo\r\n".encode("latin-1"))

    assert rejection.code is RejectionCode.UNSUPPORTED_FORMAT


@pytest.mark.parametrize(
    "params",
    [
        Params(trim_columns=("z",)),
        Params(case_rules=(CaseRule("z", CaseMode.LOWER),)),
        Params(filters=(Filter("z", FilterOp.IS_EMPTY),)),
        Params(deduplicate_by=("z",)),
        Params(output_columns=("a", "z")),
    ],
)
def test_coluna_inexistente_em_qualquer_opcao_rejeita_a_tabela(params: Params) -> None:
    rejection = rejected("a,b\r\n1,2\r\n", params)

    assert rejection.code is RejectionCode.CSV_STRUCTURE_INVALID
    assert "['z']" in rejection.reason


def test_referencias_sao_conferidas_contra_o_cabecalho_de_cada_tabela() -> None:
    params = Params(deduplicate_by=("id",))

    assert cleaned("id\r\n1\r\n", params).counts.written == 1
    assert rejected("ID\r\n1\r\n", params).code is RejectionCode.CSV_STRUCTURE_INVALID
