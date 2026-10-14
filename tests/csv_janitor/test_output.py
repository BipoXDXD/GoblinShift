"""Especificação da saída e do aviso de fórmulas (docs/GoblinShiftPlan.md, 1.3 "Saídas")."""

import pytest

from goblinshift.processing.csv_janitor import Delimiter, FormulaGuard, Params, WarningCode
from tests.csv_janitor.support import cleaned, output


def test_saida_usa_crlf_sem_bom() -> None:
    content = cleaned("a,b\n1,2\n").content

    assert content == b"a,b\r\n1,2\r\n"


@pytest.mark.parametrize(
    ("field", "serialized"),
    [
        ("simples", "simples"),
        ("com espaço", "com espaço"),
        ("vírgula,dentro", '"vírgula,dentro"'),
        ('aspas"dentro', '"aspas""dentro"'),
        ("linha\nnova", '"linha\nnova"'),
        ("retorno\rdentro", '"retorno\rdentro"'),
        ("", ""),
    ],
)
def test_aspas_so_quando_necessarias(field: str, serialized: str) -> None:
    quoted_input = '"' + field.replace('"', '""') + '"'

    assert output(f"a,b\r\n{quoted_input},x\r\n") == f"a,b\r\n{serialized},x\r\n"


def test_saida_no_delimitador_configurado_cita_so_esse_delimitador() -> None:
    params = Params(output_delimiter=Delimiter.SEMICOLON)

    assert output('a,b\r\n"1,5",x;y\r\n', params) == 'a;b\r\n1,5;"x;y"\r\n'


@pytest.mark.parametrize(
    "start", ["=", "+", "-", "@", "\t", "\r", "\n", "\uff1d", "\uff0b", "\uff0d", "\uff20"]
)
def test_celula_que_comeca_como_formula_gera_aviso_e_fica_intacta(start: str) -> None:
    table = f'a\r\n"{start}1+1"\r\n'

    result = cleaned(table)

    assert result.formula_cells == {"a": 1}
    assert [w.code for w in result.warnings] == [WarningCode.FORMULA_CELLS]
    assert f"{start}1+1" in result.content.decode()


def test_aviso_de_formula_conta_por_coluna_de_saida_e_avisa_dos_negativos() -> None:
    params = Params(output_columns=("b", "a"))

    result = cleaned("a,b,c\r\n=1,-2,@3\r\n=4,5,6\r\n", params)

    assert result.formula_cells == {"b": 1, "a": 2}
    assert "falsos positivos" in result.warnings[0].detail


def test_nome_de_coluna_que_comeca_como_formula_gera_aviso_proprio_e_fica_intacto() -> None:
    result = cleaned('=HYPERLINK("x"),b\r\n1,2\r\n')

    assert result.formula_header == ('=HYPERLINK("x")',)
    assert result.formula_cells == {}
    assert [w.code for w in result.warnings] == [WarningCode.FORMULA_HEADER]
    assert result.content == b'"=HYPERLINK(""x"")",b\r\n1,2\r\n'


def test_tab_prefix_tambem_protege_o_cabecalho() -> None:
    params = Params(formula_guard=FormulaGuard.TAB_PREFIX)

    assert output("@cmd,b\r\n1,2\r\n", params) == '"\t@cmd",b\r\n1,2\r\n'


def test_aviso_do_cabecalho_considera_so_as_colunas_de_saida() -> None:
    params = Params(output_columns=("b",))

    result = cleaned("=a,b\r\n1,2\r\n", params)

    assert result.formula_header == ()
    assert result.warnings == ()


def test_tab_prefix_poe_o_tab_dentro_de_aspas_so_nas_celulas_suspeitas() -> None:
    params = Params(formula_guard=FormulaGuard.TAB_PREFIX)

    result = output("a,b\r\n=SOMA(1),ok\r\n", params)

    assert result == 'a,b\r\n"\t=SOMA(1)",ok\r\n'


def test_sem_celulas_suspeitas_nao_ha_aviso() -> None:
    assert cleaned("a\r\n1\r\n").warnings == ()
