"""Catálogo: parâmetros estritos, defaults resolvidos e estado das versões (1.3, 1.6 e 1.7)."""

import json
from dataclasses import replace
from typing import Any

import pytest

from goblinshift.catalog import (
    CATALOG,
    Catalog,
    InvalidParametersError,
    RecipeVersionRetiredError,
    UnknownRecipeVersionError,
    VersionStatus,
)
from goblinshift.catalog.csv_janitor_v1 import CsvJanitorV1Parameters
from goblinshift.processing import downloads_detox, image_forge
from goblinshift.processing.csv_janitor import (
    CaseMode,
    CaseRule,
    Delimiter,
    Filter,
    FilterOp,
    FormulaGuard,
    Params,
    params_from_mapping,
)
from goblinshift.processing.registry import RECIPES

DEFAULTS: dict[str, object] = {
    "case_rules": [],
    "deduplicate_by": [],
    "filters": [],
    "formula_guard": "none",
    "input_delimiter": ",",
    "output_columns": None,
    "output_delimiter": ",",
    "trim_columns": [],
}


def resolve(raw: Any) -> dict[str, object]:
    return CATALOG.resolve("csv-janitor", 1, raw)


def error_locations(raw: Any) -> list[str]:
    with pytest.raises(InvalidParametersError) as error:
        resolve(raw)
    return [field.location for field in error.value.errors]


def test_parametros_vazios_recebem_todos_os_defaults() -> None:
    assert resolve({}) == DEFAULTS


def test_defaults_resolvidos_sao_os_defaults_da_biblioteca() -> None:
    assert params_from_mapping(resolve({})) == Params()


def test_resolve_ordena_as_chaves_e_preserva_a_ordem_das_listas() -> None:
    raw = {
        "output_columns": ["b", "a"],
        "filters": [{"value": "x", "op": "eq", "column": "a"}],
    }

    resolved = resolve(raw)

    assert list(resolved) == sorted(resolved)
    assert resolved["output_columns"] == ["b", "a"]
    assert resolved["filters"] == [{"column": "a", "op": "eq", "value": "x"}]


def test_mesmo_comando_com_chaves_em_outra_ordem_resolve_igual() -> None:
    first = resolve({"trim_columns": ["a"], "formula_guard": "tab_prefix", "input_delimiter": ";"})
    second = resolve({"input_delimiter": ";", "formula_guard": "tab_prefix", "trim_columns": ["a"]})

    assert json.dumps(first) == json.dumps(second)


def test_omitir_default_ou_informa_lo_resolve_igual() -> None:
    assert resolve({"formula_guard": "none", "filters": []}) == resolve({})


def test_parametros_resolvidos_viram_os_params_da_biblioteca() -> None:
    raw = {
        "input_delimiter": ";",
        "output_delimiter": "\t",
        "trim_columns": ["nome"],
        "case_rules": [{"column": "uf", "mode": "upper"}],
        "filters": [
            {"column": "uf", "op": "ne", "value": "SP"},
            {"column": "obs", "op": "is_empty"},
        ],
        "deduplicate_by": ["nome", "uf"],
        "output_columns": ["uf", "nome"],
        "formula_guard": "tab_prefix",
    }
    expected = Params(
        input_delimiter=Delimiter.SEMICOLON,
        output_delimiter=Delimiter.TAB,
        trim_columns=("nome",),
        case_rules=(CaseRule("uf", CaseMode.UPPER),),
        filters=(Filter("uf", FilterOp.NE, "SP"), Filter("obs", FilterOp.IS_EMPTY)),
        deduplicate_by=("nome", "uf"),
        output_columns=("uf", "nome"),
        formula_guard=FormulaGuard.TAB_PREFIX,
    )

    assert CsvJanitorV1Parameters.model_validate(raw).to_params() == expected
    assert params_from_mapping(resolve(raw)) == expected


def test_campo_desconhecido_e_recusado() -> None:
    assert error_locations({"trim": ["a"]}) == ["trim"]


def test_campo_desconhecido_dentro_de_filtro_e_recusado() -> None:
    raw = {"filters": [{"column": "a", "op": "eq", "value": "x", "regex": "."}]}

    assert error_locations(raw) == ["filters.0.regex"]


@pytest.mark.parametrize(
    ("raw", "location"),
    [
        ({"trim_columns": "a"}, "trim_columns"),
        ({"trim_columns": [1]}, "trim_columns.0"),
        ({"input_delimiter": "|"}, "input_delimiter"),
        ({"formula_guard": True}, "formula_guard"),
        ({"case_rules": [{"column": "a", "mode": "title"}]}, "case_rules.0.mode"),
        ({"output_columns": []}, "output_columns"),
        ({"filters": [{"column": "a", "op": "eq", "value": 1}]}, "filters.0.value"),
    ],
)
def test_tipo_ou_valor_errado_e_recusado_no_campo(raw: Any, location: str) -> None:
    assert error_locations(raw) == [location]


def test_parametros_que_nao_sao_objeto_sao_recusados() -> None:
    assert error_locations(["a"]) == [""]


def test_cinco_filtros_sao_aceitos_e_seis_recusados() -> None:
    five = [{"column": "a", "op": "is_empty"}] * 5

    assert resolve({"filters": five})["filters"] == [{**f, "value": None} for f in five]
    assert error_locations({"filters": [*five, five[0]]}) == ["filters"]


@pytest.mark.parametrize(
    ("raw", "location"),
    [
        ({"trim_columns": ["a", "a"]}, "trim_columns"),
        ({"deduplicate_by": ["a", "a"]}, "deduplicate_by"),
        ({"output_columns": ["a", "a"]}, "output_columns"),
        (
            {"case_rules": [{"column": "a", "mode": "lower"}, {"column": "a", "mode": "upper"}]},
            "case_rules",
        ),
        ({"filters": [{"column": "a", "op": "is_empty", "value": "x"}]}, "filters.0"),
        ({"filters": [{"column": "a", "op": "eq"}]}, "filters.0"),
        ({"trim_columns": [""]}, "trim_columns.0"),
    ],
)
def test_combinacao_invalida_vira_erro_no_campo(raw: Any, location: str) -> None:
    assert error_locations(raw) == [location]


def test_versao_inexistente_e_recusada() -> None:
    with pytest.raises(UnknownRecipeVersionError):
        CATALOG.resolve("csv-janitor", 2, {})
    with pytest.raises(UnknownRecipeVersionError):
        CATALOG.get_version("nada", 1)


def test_versao_retirada_continua_legivel_mas_nao_resolve() -> None:
    current = CATALOG.get_version("csv-janitor", 1)
    catalog = Catalog([replace(current, status=VersionStatus.RETIRED)])

    assert catalog.get_version("csv-janitor", 1).status is VersionStatus.RETIRED
    with pytest.raises(RecipeVersionRetiredError):
        catalog.resolve("csv-janitor", 1, {})


def test_versao_atual_publica_limites_efetivos() -> None:
    version = CATALOG.get_version("csv-janitor", 1)

    assert version.status is VersionStatus.CURRENT
    assert version.limits == {
        "max_inputs": 5,
        "table_bytes": 8 * 1024 * 1024,
        "records": 20_000,
        "columns": 100,
        "record_bytes": 1024 * 1024,
        "field_bytes": 64 * 1024,
    }


def test_toda_versao_nao_retirada_tem_implementacao_no_registro() -> None:
    for version in CATALOG.list_versions():
        if version.status is not VersionStatus.RETIRED:
            assert (version.recipe_id, version.version) in RECIPES


def test_schema_da_variante_fixa_receita_e_versao_e_recusa_campos_extras() -> None:
    schema = CATALOG.get_version("csv-janitor", 1).variant_schema()

    assert schema["properties"]["recipe_id"] == {"const": "csv-janitor"}
    assert schema["properties"]["recipe_version"] == {"const": 1}
    parameters = schema["properties"]["parameters"]
    assert parameters["additionalProperties"] is False
    assert parameters["properties"]["filters"]["maxItems"] == 5
    assert parameters["properties"]["input_delimiter"]["enum"] == [",", ";", "\t"]
    assert parameters["properties"]["input_delimiter"]["default"] == ","
    filter_item = parameters["properties"]["filters"]["items"]
    assert filter_item["additionalProperties"] is False
    assert "$ref" not in json.dumps(schema)


# --- Receitas da fatia 5 -------------------------------------------------------------------


def test_catalogo_lista_as_receitas_na_versao_1() -> None:
    keys = {(version.recipe_id, version.version) for version in CATALOG.list_versions()}

    assert keys == {
        ("csv-janitor", 1),
        ("image-forge", 1),
        ("downloads-detox", 1),
        ("media-mill", 1),
    }


def test_image_forge_publica_os_limites_de_1_7() -> None:
    version = CATALOG.get_version("image-forge", 1)

    assert version.status is VersionStatus.CURRENT
    assert version.limits == {
        "max_inputs": 100,
        "max_pixels": 24_000_000,
        "max_axis_px": 8192,
        "thumbnail_box_px": 320,
    }


def test_downloads_detox_publica_o_teto_do_lote_abaixo_do_teto_de_saidas() -> None:
    version = CATALOG.get_version("downloads-detox", 1)

    assert version.status is VersionStatus.CURRENT
    assert version.limits == {"max_inputs": 100, "max_total_input_bytes": 480 * 1024 * 1024}


def test_image_forge_resolve_os_defaults_e_vira_os_params_da_biblioteca() -> None:
    resolved = CATALOG.resolve("image-forge", 1, {})

    assert resolved == {
        "jpeg_background": "#FFFFFF",
        "jpeg_quality": 85,
        "make_thumbnails": False,
        "max_height": 1600,
        "max_width": 1600,
        "output_format": "preserve",
    }
    assert image_forge.params_from_mapping(resolved) == image_forge.Params()


def test_downloads_detox_resolve_os_defaults_e_vira_os_params_da_biblioteca() -> None:
    resolved = CATALOG.resolve("downloads-detox", 1, {})

    assert resolved == {
        "deduplicate": True,
        "group_by": "kind",
        "name_mode": "preserve",
        "name_prefix": "file",
    }
    assert downloads_detox.params_from_mapping(resolved) == downloads_detox.Params()


@pytest.mark.parametrize("recipe_id", ["image-forge", "downloads-detox"])
def test_receita_nova_recusa_campo_desconhecido_no_campo(recipe_id: str) -> None:
    with pytest.raises(InvalidParametersError) as error:
        CATALOG.resolve(recipe_id, 1, {"inventado": 1})

    assert [field.location for field in error.value.errors] == ["inventado"]
