"""Especificação da configuração (docs/GoblinShiftPlan.md, 1.3, tabela de opções)."""

import re

import pytest

from goblinshift.processing.csv_janitor import (
    CaseMode,
    CaseRule,
    Delimiter,
    Filter,
    FilterOp,
    FormulaGuard,
    InvalidParametersError,
    Params,
    params_from_mapping,
)


def test_defaults_sao_virgula_e_nenhuma_transformacao() -> None:
    assert params_from_mapping({}) == Params(
        input_delimiter=Delimiter.COMMA,
        output_delimiter=Delimiter.COMMA,
        trim_columns=(),
        case_rules=(),
        filters=(),
        deduplicate_by=(),
        output_columns=None,
        formula_guard=FormulaGuard.NONE,
    )


def test_converte_a_configuracao_completa() -> None:
    raw: dict[str, object] = {
        "input_delimiter": "\t",
        "output_delimiter": ";",
        "trim_columns": ["nome"],
        "case_rules": [{"column": "email", "mode": "lower"}],
        "filters": [
            {"column": "ativo", "op": "eq", "value": "s"},
            {"column": "obs", "op": "is_empty"},
        ],
        "deduplicate_by": ["email"],
        "output_columns": ["email", "nome"],
        "formula_guard": "tab_prefix",
    }

    assert params_from_mapping(raw) == Params(
        input_delimiter=Delimiter.TAB,
        output_delimiter=Delimiter.SEMICOLON,
        trim_columns=("nome",),
        case_rules=(CaseRule("email", CaseMode.LOWER),),
        filters=(Filter("ativo", FilterOp.EQ, "s"), Filter("obs", FilterOp.IS_EMPTY)),
        deduplicate_by=("email",),
        output_columns=("email", "nome"),
        formula_guard=FormulaGuard.TAB_PREFIX,
    )


@pytest.mark.parametrize(
    ("raw", "reason"),
    [
        ({"extra": 1}, "desconhecidas"),
        ({"input_delimiter": "|"}, "input_delimiter deve ser um de"),
        ({"trim_columns": "nome"}, "lista de textos"),
        ({"trim_columns": [1]}, "texto"),
        ({"case_rules": [{"column": "a", "mode": "title"}]}, "case_rules.mode"),
        ({"case_rules": [{"column": "a"}]}, "faltando ['mode']"),
        ({"filters": [{"column": "a", "op": "regex", "value": "x"}]}, "filters.op"),
        ({"filters": [{"column": "a", "op": "eq", "value": "x", "x": 1}]}, "desconhecidos ['x']"),
        ({"filters": "a"}, "lista de objetos"),
        ({"formula_guard": "quote"}, "formula_guard"),
    ],
)
def test_configuracao_com_campo_desconhecido_ou_tipo_errado_e_recusada(
    raw: dict[str, object], reason: str
) -> None:
    with pytest.raises(InvalidParametersError, match=re.escape(reason)):
        params_from_mapping(raw)


@pytest.mark.parametrize(
    ("build", "reason"),
    [
        (lambda: Params(filters=(Filter("a", FilterOp.IS_EMPTY),) * 6), "no máximo 5 filtros"),
        (
            lambda: Params(
                case_rules=(CaseRule("a", CaseMode.LOWER), CaseRule("a", CaseMode.UPPER))
            ),
            "case_rules repete colunas",
        ),
        (lambda: Params(output_columns=()), "output_columns vazia"),
        (lambda: Params(output_columns=("a", "a")), "output_columns repete"),
        (lambda: Params(trim_columns=("a", "a")), "trim_columns repete"),
        (lambda: Params(deduplicate_by=("a", "a")), "deduplicate_by repete"),
        (lambda: Params(trim_columns=("",)), "nome de coluna vazio"),
        (lambda: Filter("a", FilterOp.EQ), "exige um valor"),
        (lambda: Filter("a", FilterOp.IS_EMPTY, "x"), "não usa valor"),
    ],
)
def test_combinacoes_invalidas_sao_recusadas_antes_de_ler_tabelas(
    build: object, reason: str
) -> None:
    with pytest.raises(InvalidParametersError, match=reason):
        build()  # type: ignore[operator]


def test_cinco_filtros_sao_aceitos() -> None:
    assert len(Params(filters=(Filter("a", FilterOp.IS_EMPTY),) * 5).filters) == 5
