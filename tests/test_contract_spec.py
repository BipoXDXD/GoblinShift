"""Spec derivada para o Schemathesis (`scripts/contract_spec.py`).

O gerador do Schemathesis não suporta `unevaluatedProperties`, que o corpo de `POST /runs` usa
sobre `allOf` + `oneOf` (`entrypoints.api.runs`). A derivação reescreve só esse corpo como um
`oneOf` de objetos fechados, equivalente, e falha alto se o formato mudar, para o teste de
contrato nunca exercitar em silêncio um corpo diferente do publicado.
"""

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from goblinshift.entrypoints.openapi_export import render_openapi
from scripts import contract_spec

COMMON = {
    "type": "object",
    "properties": {"input_upload_ids": {"type": "array", "maxItems": 100}},
}
VARIANT_A = {
    "title": "a v1",
    "type": "object",
    "properties": {"recipe_id": {"const": "a"}, "parameters": {"type": "object"}},
    "required": ["recipe_id", "parameters"],
}
VARIANT_B = {
    "title": "b v1",
    "type": "object",
    "properties": {"recipe_id": {"const": "b"}},
    "required": ["recipe_id"],
}


def _spec(body: dict[str, Any]) -> dict[str, Any]:
    content = {"application/json": {"schema": body}}
    return {"paths": {"/x": {"post": {"requestBody": {"content": content}}}}}


def _body(spec: dict[str, Any]) -> Any:
    return spec["paths"]["/x"]["post"]["requestBody"]["content"]["application/json"]["schema"]


def test_flattens_allof_with_unevaluated_properties_into_closed_variants() -> None:
    spec = _spec(
        {"allOf": [COMMON, {"oneOf": [VARIANT_A, VARIANT_B]}], "unevaluatedProperties": False}
    )

    derived = contract_spec.derive(spec)

    assert _body(derived) == {
        "oneOf": [
            {
                "title": "a v1",
                "type": "object",
                "properties": {
                    "input_upload_ids": {"type": "array", "maxItems": 100},
                    "recipe_id": {"const": "a"},
                    "parameters": {"type": "object"},
                },
                "required": ["recipe_id", "parameters"],
                "additionalProperties": False,
            },
            {
                "title": "b v1",
                "type": "object",
                "properties": {
                    "input_upload_ids": {"type": "array", "maxItems": 100},
                    "recipe_id": {"const": "b"},
                },
                "required": ["recipe_id"],
                "additionalProperties": False,
            },
        ]
    }


def test_required_common_fields_join_each_variant_required() -> None:
    common = {
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "required": ["name"],
    }
    spec = _spec(
        {"allOf": [common, {"oneOf": [VARIANT_A, VARIANT_B]}], "unevaluatedProperties": False}
    )

    variants = _body(contract_spec.derive(spec))["oneOf"]

    assert [variant["required"] for variant in variants] == [
        ["name", "recipe_id", "parameters"],
        ["name", "recipe_id"],
    ]
    assert all(variant["properties"]["name"] == {"type": "string"} for variant in variants)


def test_does_not_mutate_the_input_spec() -> None:
    spec = _spec({"allOf": [COMMON, {"oneOf": [VARIANT_A]}], "unevaluatedProperties": False})
    before = copy.deepcopy(spec)

    contract_spec.derive(spec)

    assert spec == before


def test_leaves_a_spec_without_unevaluated_properties_unchanged() -> None:
    spec = _spec({"type": "object", "properties": {"name": {"type": "string"}}})

    assert contract_spec.derive(spec) == spec


@pytest.mark.parametrize(
    "body",
    [
        {"unevaluatedProperties": False, "type": "object"},
        {"allOf": [COMMON], "unevaluatedProperties": False},
        {"allOf": [COMMON, {"anyOf": [VARIANT_A]}], "unevaluatedProperties": False},
        {"allOf": [COMMON, {"oneOf": [VARIANT_A]}], "unevaluatedProperties": True},
        {
            "allOf": [{**COMMON, "minProperties": 1}, {"oneOf": [VARIANT_A]}],
            "unevaluatedProperties": False,
        },
        {
            "allOf": [{"type": "object", "properties": {"recipe_id": {}}}, {"oneOf": [VARIANT_A]}],
            "unevaluatedProperties": False,
        },
    ],
    ids=[
        "sem-allOf",
        "allOf-sem-variantes",
        "variantes-em-anyOf",
        "unevaluated-true",
        "comum-com-chave-desconhecida",
        "campo-comum-repetido-na-variante",
    ],
)
def test_rejects_an_unknown_shape(body: dict[str, Any]) -> None:
    with pytest.raises(contract_spec.UnsupportedShapeError):
        contract_spec.derive(_spec(body))


def test_derives_the_real_spec_without_any_unevaluated_properties_left() -> None:
    derived = contract_spec.derive(json.loads(render_openapi()))

    assert "unevaluatedProperties" not in json.dumps(derived)


def test_main_writes_the_derived_spec(tmp_path: Path) -> None:
    source = tmp_path / "openapi.json"
    target = tmp_path / "derived.json"
    source.write_text(render_openapi(), encoding="utf-8")

    assert contract_spec.main([str(source), str(target)]) == 0

    assert json.loads(target.read_text(encoding="utf-8")) == contract_spec.derive(
        json.loads(source.read_text(encoding="utf-8"))
    )
