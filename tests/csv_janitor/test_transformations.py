"""Especificação das transformações (docs/GoblinShiftPlan.md, 1.3, opções e ordem fixa)."""

import pytest

from goblinshift.processing.csv_janitor import (
    CaseMode,
    CaseRule,
    Counts,
    Filter,
    FilterOp,
    Params,
    WarningCode,
)
from tests.csv_janitor.support import cleaned, output


def test_campos_sao_sempre_texto_sem_inferencia() -> None:
    table = (
        "id,valor,data,flag,nulo\r\n"
        "00123,1e5,2026-01-02,true,\r\n"
        "12345678901234567890,1.0,01/02,NULL,\r\n"
    )

    assert output(table) == table


def test_trim_tira_so_os_espacos_externos_das_colunas_listadas() -> None:
    params = Params(trim_columns=("a",))

    assert output("a,b\r\n  x  y\t, z \r\n", params) == "a,b\r\nx  y, z \r\n"


def test_trim_nao_mexe_no_cabecalho() -> None:
    params = Params(trim_columns=(" nome ",))

    assert output(" nome \r\n  Ana  \r\n", params) == " nome \r\nAna\r\n"


@pytest.mark.parametrize(
    ("mode", "expected"),
    [(CaseMode.LOWER, "rua são joão"), (CaseMode.UPPER, "RUA SÃO JOÃO")],
)
def test_caixa_usa_as_regras_unicode(mode: CaseMode, expected: str) -> None:
    params = Params(case_rules=(CaseRule("a", mode),))

    assert output("a,b\r\nRua São João,x\r\n", params) == f"a,b\r\n{expected},x\r\n"


def test_caixa_alta_pode_mudar_o_tamanho_do_texto() -> None:
    params = Params(case_rules=(CaseRule("a", CaseMode.UPPER),))

    assert output("a\r\nstraße\r\n", params) == "a\r\nSTRASSE\r\n"


@pytest.mark.parametrize(
    ("predicate", "kept"),
    [
        (Filter("cor", FilterOp.EQ, "azul"), ["1"]),
        (Filter("cor", FilterOp.NE, "azul"), ["2", "3", "4"]),
        (Filter("cor", FilterOp.CONTAINS, "ul"), ["1", "2"]),
        (Filter("cor", FilterOp.IS_EMPTY), ["4"]),
    ],
)
def test_cada_operador_de_filtro_compara_texto(predicate: Filter, kept: list[str]) -> None:
    table = "id,cor\r\n1,azul\r\n2,Azul\r\n3,verde\r\n4,\r\n"

    survivors = output(table, Params(filters=(predicate,))).splitlines()[1:]

    assert [line.split(",")[0] for line in survivors] == kept


def test_filtro_nao_interpreta_numeros() -> None:
    params = Params(filters=(Filter("v", FilterOp.EQ, "1"),))

    assert output("v\r\n1\r\n1.0\r\n01\r\n", params) == "v\r\n1\r\n"


def test_filtros_combinam_por_e() -> None:
    params = Params(
        filters=(Filter("cor", FilterOp.EQ, "azul"), Filter("tam", FilterOp.NE, "P")),
    )

    result = output("cor,tam\r\nazul,P\r\nazul,G\r\nverde,G\r\n", params)

    assert result == "cor,tam\r\nazul,G\r\n"


def test_normalizacao_acontece_antes_do_filtro() -> None:
    params = Params(
        trim_columns=("cor",),
        case_rules=(CaseRule("cor", CaseMode.LOWER),),
        filters=(Filter("cor", FilterOp.EQ, "azul"),),
    )

    assert output("cor\r\n  AZUL \r\nverde\r\n", params) == "cor\r\nazul\r\n"


def test_deduplicacao_preserva_a_primeira_ocorrencia_na_ordem_original() -> None:
    params = Params(deduplicate_by=("email",))
    table = "email,n\r\nb@x,1\r\na@x,2\r\nb@x,3\r\nc@x,4\r\na@x,5\r\n"

    assert output(table, params) == "email,n\r\nb@x,1\r\na@x,2\r\nc@x,4\r\n"


def test_deduplicacao_compara_tuplas_sem_separador_ambiguo() -> None:
    # Concatenar com "|" tornaria ("a|", "b") e ("a", "|b") a mesma chave.
    params = Params(deduplicate_by=("x", "y"))

    assert cleaned("x,y\r\na|,b\r\na,|b\r\n", params).counts.duplicates_removed == 0


def test_deduplicacao_acontece_depois_da_normalizacao() -> None:
    params = Params(case_rules=(CaseRule("e", CaseMode.LOWER),), deduplicate_by=("e",))

    assert output("e\r\nAna@X\r\nana@x\r\n", params) == "e\r\nana@x\r\n"


def test_deduplicacao_acontece_antes_da_projecao() -> None:
    params = Params(deduplicate_by=("id",), output_columns=("nome",))

    assert output("id,nome\r\n1,Ana\r\n1,Bia\r\n2,Ana\r\n", params) == "nome\r\nAna\r\nAna\r\n"


def test_sem_colunas_de_deduplicacao_nada_e_removido() -> None:
    assert cleaned("a\r\nx\r\nx\r\n").counts.duplicates_removed == 0


def test_colunas_finais_selecionam_e_ordenam() -> None:
    params = Params(output_columns=("c", "a"))

    assert output("a,b,c\r\n1,2,3\r\n", params) == "c,a\r\n3,1\r\n"


def test_sem_colunas_finais_todas_ficam_na_ordem_original() -> None:
    assert output("c,a,b\r\n3,1,2\r\n") == "c,a,b\r\n3,1,2\r\n"


def test_contagem_fecha_lidos_igual_a_filtrados_mais_duplicados_mais_sobreviventes() -> None:
    params = Params(filters=(Filter("ok", FilterOp.EQ, "s"),), deduplicate_by=("id",))
    table = "id,ok\r\n1,s\r\n\r\n2,n\r\n1,s\r\n3,s\r\n4,n\r\n"

    counts = cleaned(table, params).counts

    assert counts == Counts(
        records_read=5, filtered_out=2, duplicates_removed=1, written=2, blank_lines_ignored=1
    )


def test_filtro_que_remove_tudo_gera_so_o_cabecalho_com_aviso() -> None:
    params = Params(filters=(Filter("a", FilterOp.EQ, "nada"),))

    result = cleaned("a,b\r\n1,2\r\n3,4\r\n", params)

    assert result.content == b"a,b\r\n"
    assert [w.code for w in result.warnings] == [WarningCode.ALL_RECORDS_FILTERED]
