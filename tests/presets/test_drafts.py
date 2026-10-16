"""Rascunho de preset: nome e configuração validados pelo catálogo, sem banco (1.6 e 3.3)."""

from dataclasses import replace

import pytest

from goblinshift.catalog import CATALOG, Catalog, RecipeVersionRetiredError, VersionStatus
from goblinshift.presets import InvalidPresetError, parse_draft
from goblinshift.presets import drafts as drafts_module

CSV_DEFAULTS: dict[str, object] = {
    "case_rules": [],
    "deduplicate_by": [],
    "filters": [],
    "formula_guard": "none",
    "input_delimiter": ",",
    "output_columns": None,
    "output_delimiter": ",",
    "trim_columns": [],
}


def locations(error: pytest.ExceptionInfo[InvalidPresetError]) -> list[str]:
    return [field.location for field in error.value.errors]


def test_rascunho_guarda_os_parametros_com_os_defaults_da_versao() -> None:
    draft = parse_draft("Limpeza mensal", "csv-janitor", 1, {"trim_columns": ["a"]})

    assert draft.name == "Limpeza mensal"
    assert draft.recipe.recipe_id == "csv-janitor"
    assert draft.recipe.version == 1
    assert draft.parameters == {**CSV_DEFAULTS, "trim_columns": ["a"]}


def test_parametro_invalido_vira_erro_no_campo_sob_parameters() -> None:
    with pytest.raises(InvalidPresetError) as error:
        parse_draft("x", "csv-janitor", 1, {"formula_guard": "talvez"})

    assert locations(error) == ["parameters.formula_guard"]


def test_versao_desconhecida_vira_erro_no_campo_recipe_version() -> None:
    with pytest.raises(InvalidPresetError) as error:
        parse_draft("x", "csv-janitor", 2, {})

    assert locations(error) == ["recipe_version"]


def test_versao_retirada_e_recusada(monkeypatch: pytest.MonkeyPatch) -> None:
    retired = replace(CATALOG.get_version("csv-janitor", 1), status=VersionStatus.RETIRED)
    monkeypatch.setattr(drafts_module, "CATALOG", Catalog([retired]))

    with pytest.raises(RecipeVersionRetiredError):
        parse_draft("x", "csv-janitor", 1, {})


@pytest.mark.parametrize(
    "name",
    ["", "   ", "a" * 101, "linha\nnova", "tab\tulado", "nul\u0000", "‮evil", "zero​width"],
    ids=["vazio", "so-espacos", "101-caracteres", "quebra", "tab", "nul", "bidi", "cf"],
)
def test_nome_invalido_e_recusado_no_campo_name(name: str) -> None:
    with pytest.raises(InvalidPresetError) as error:
        parse_draft(name, "csv-janitor", 1, {})

    assert locations(error) == ["name"]


def test_nome_com_100_caracteres_e_aceito() -> None:
    assert parse_draft("a" * 100, "csv-janitor", 1, {}).name == "a" * 100


def test_nome_e_normalizado_para_nfc() -> None:
    decomposed = "Café"

    assert parse_draft(decomposed, "csv-janitor", 1, {}).name == "Café"


def test_nome_e_parametros_invalidos_aparecem_juntos() -> None:
    with pytest.raises(InvalidPresetError) as error:
        parse_draft("", "csv-janitor", 1, {"inventado": 1})

    assert locations(error) == ["name", "parameters.inventado"]
