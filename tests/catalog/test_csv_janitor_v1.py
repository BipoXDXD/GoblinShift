"""Teto dos textos livres dos parâmetros do csv-janitor v1 (#442, item 44d; decisão D2 = A)."""

import pytest

from goblinshift.catalog import InvalidParametersError
from goblinshift.catalog.versions import CSV_JANITOR_V1


def accepts(parameters: dict[str, object]) -> bool:
    try:
        CSV_JANITOR_V1.resolve(parameters)
    except InvalidParametersError:
        return False
    return True


@pytest.mark.parametrize(
    ("column", "ok"), [("c" * 1024, True), ("c" * 1025, False)], ids=["1024", "1025"]
)
def test_teto_do_nome_de_coluna(column: str, ok: bool) -> None:
    assert accepts({"trim_columns": [column]}) is ok


@pytest.mark.parametrize(
    ("value", "ok"), [("v" * 4096, True), ("v" * 4097, False)], ids=["4096", "4097"]
)
def test_teto_do_valor_do_filtro(value: str, ok: bool) -> None:
    assert accepts({"filters": [{"column": "status", "op": "eq", "value": value}]}) is ok
