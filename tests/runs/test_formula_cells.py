"""Contagem por coluna do aviso CSV_FORMULA_CELLS no banco e na API (1.3; ADR 039)."""

import pytest

from goblinshift.runs.formula_cells import (
    MAX_COLUMN_NAME_CHARS,
    MAX_COLUMNS,
    ColumnCells,
    FormulaCells,
)


def test_colunas_saem_da_maior_contagem_para_a_menor_com_empate_na_ordem_da_saida() -> None:
    summary = FormulaCells.of([("a", 1), ("b", 3), ("c", 1), ("d", 2)])

    assert summary == FormulaCells(
        (ColumnCells("b", 3), ColumnCells("d", 2), ColumnCells("a", 1), ColumnCells("c", 1)), 0
    )


def test_dez_colunas_cabem_inteiras() -> None:
    summary = FormulaCells.of([(f"c{i}", 1) for i in range(10)])

    assert MAX_COLUMNS == 10
    assert len(summary.columns) == 10
    assert summary.more_columns == 0


def test_onze_colunas_guardam_dez_e_contam_a_que_ficou_de_fora() -> None:
    summary = FormulaCells.of([(f"c{i}", 20 - i) for i in range(11)])

    assert [column.name for column in summary.columns] == [f"c{i}" for i in range(10)]
    assert summary.more_columns == 1


def test_nome_no_teto_fica_intacto_e_um_acima_e_cortado_com_reticencias() -> None:
    at_limit = "x" * MAX_COLUMN_NAME_CHARS
    above = "y" * (MAX_COLUMN_NAME_CHARS + 1)

    summary = FormulaCells.of([(at_limit, 2), (above, 1)])

    assert MAX_COLUMN_NAME_CHARS == 120
    assert summary.columns[0].name == at_limit
    assert summary.columns[1].name == "y" * (MAX_COLUMN_NAME_CHARS - 1) + "…"


@pytest.mark.parametrize("char", ["\x00", "\t", "\u202e", "\u200b"])
def test_controle_e_formato_invisivel_viram_caractere_de_substituicao(char: str) -> None:
    summary = FormulaCells.of([(f"a{char}b", 1)])

    assert summary.columns[0].name == "a\ufffdb"


def test_ida_e_volta_pelo_json_do_banco() -> None:
    summary = FormulaCells.of([("valor", 2), ("conta", 1)])

    stored = summary.as_json()

    assert stored == {
        "columns": [{"name": "valor", "cells": 2}, {"name": "conta", "cells": 1}],
        "more_columns": 0,
    }
    assert FormulaCells.from_json(stored) == summary


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param(None, id="ausente"),
        pytest.param([], id="nao-objeto"),
        pytest.param({"columns": [], "more_columns": 0}, id="sem-colunas"),
        pytest.param({"columns": [{"name": "a", "cells": 1}]}, id="sem-more"),
        pytest.param({"columns": [{"name": "a", "cells": 0}], "more_columns": 0}, id="zero"),
        pytest.param({"columns": [{"name": "a", "cells": True}], "more_columns": 0}, id="bool"),
        pytest.param({"columns": [{"name": 1, "cells": 1}], "more_columns": 0}, id="nome"),
        pytest.param({"columns": [{"name": "", "cells": 1}], "more_columns": 0}, id="vazio"),
        pytest.param({"columns": [{"name": "a", "cells": 1}], "more_columns": -1}, id="negativo"),
        pytest.param(
            {"columns": [{"name": "a", "cells": 1}] * 11, "more_columns": 0}, id="acima-do-teto"
        ),
        pytest.param(
            {"columns": [{"name": "a" * 121, "cells": 1}], "more_columns": 0}, id="nome-longo"
        ),
    ],
)
def test_json_fora_da_forma_vira_ausencia(raw: object) -> None:
    assert FormulaCells.from_json(raw) is None
