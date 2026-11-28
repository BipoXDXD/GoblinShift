"""Parâmetros do image-forge v2 no catálogo: superconjunto da v1, enums próprios e sem PIL."""

import subprocess
import sys
from dataclasses import replace
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from goblinshift.catalog import CATALOG
from goblinshift.catalog.image_forge_v2 import ImageForgeV2Parameters
from goblinshift.processing.image_forge import params_from_mapping, params_v2_from_mapping
from goblinshift.processing.image_forge.geometry import FitMode
from goblinshift.processing.image_forge.inspection import V2_FORMATS
from goblinshift.processing.image_forge.params import HexColor
from goblinshift.processing.image_forge.params_v2 import OutputFormatV2, ParamsV2
from goblinshift.processing.image_forge.plan import plan_from_v1, plan_from_v2

DEFAULTS = {
    "fit": "contain",
    "jpeg_background": "#FFFFFF",
    "jpeg_quality": 85,
    "make_thumbnails": False,
    "max_height": 1600,
    "max_width": 1600,
    "output_format": "preserve",
    "scale_percent": 100,
    "webp_lossless": False,
    "webp_quality": 80,
}

_side = st.integers(min_value=16, max_value=4096)
_hex = st.integers(min_value=0, max_value=0xFFFFFF).map(lambda value: f"#{value:06x}")


@st.composite
def v1_params(draw: st.DrawFn) -> dict[str, object]:
    """Parâmetros válidos da v1, com campos omitidos ao acaso (os defaults entram no catálogo)."""
    output_format = draw(st.sampled_from(["preserve", "jpeg", "png"]))
    raw: dict[str, object] = {
        "max_width": draw(_side),
        "max_height": draw(_side),
        "output_format": output_format,
        "make_thumbnails": draw(st.booleans()),
    }
    if output_format != "png":
        raw["jpeg_quality"] = draw(st.integers(min_value=60, max_value=95))
        raw["jpeg_background"] = draw(_hex)
    keep = draw(st.sets(st.sampled_from(sorted(raw))))
    return {name: value for name, value in raw.items() if name in keep}


@given(v1_params())
def test_parametros_da_v1_valem_na_v2_com_o_mesmo_plano(raw: dict[str, object]) -> None:
    v1 = CATALOG.resolve("image-forge", 1, raw)
    v2 = CATALOG.resolve("image-forge", 2, v1)
    expected = replace(plan_from_v1(params_from_mapping(v1)), formats=V2_FORMATS, fit_details=True)
    assert plan_from_v2(params_v2_from_mapping(v2)) == expected


def test_defaults_resolvidos_da_v2() -> None:
    assert ImageForgeV2Parameters.model_validate({}).model_dump(mode="json") == DEFAULTS


def test_converte_para_params_v2_da_biblioteca() -> None:
    model = ImageForgeV2Parameters.model_validate(
        {"fit": "pad", "output_format": "jpeg", "jpeg_background": "#ff0000", "max_width": 400}
    )
    assert model.to_params() == ParamsV2(
        fit=FitMode.PAD,
        output_format=OutputFormatV2.JPEG,
        jpeg_background=HexColor(255, 0, 0),
        max_width=400,
    )


def test_combinacao_invalida_vira_erro_de_validacao() -> None:
    with pytest.raises(ValidationError, match="scale_percent"):
        ImageForgeV2Parameters.model_validate({"scale_percent": 50})


@pytest.mark.parametrize(
    ("raw", "location"),
    [
        ({"fit": "zoom"}, "fit"),
        ({"output_format": "gif"}, "output_format"),
        ({"scale_percent": 0}, "scale_percent"),
        ({"webp_quality": 96}, "webp_quality"),
        ({"webp_lossless": 1}, "webp_lossless"),
        ({"quality": 80}, "quality"),
    ],
)
def test_valor_invalido_aponta_o_campo(raw: dict[str, Any], location: str) -> None:
    with pytest.raises(ValidationError) as caught:
        ImageForgeV2Parameters.model_validate(raw)
    assert [error["loc"][0] for error in caught.value.errors()] == [location]


def test_schema_da_v2_publica_os_enums_novos() -> None:
    schema = ImageForgeV2Parameters.model_json_schema()

    def field(name: str) -> dict[str, Any]:
        node: dict[str, Any] = schema["properties"][name]
        if "$ref" in node:
            node = {**schema["$defs"][node["$ref"].removeprefix("#/$defs/")], **node}
        return node

    assert schema["title"] == "ImageForgeV2Parameters"
    assert field("output_format")["enum"] == ["preserve", "jpeg", "png", "webp"]
    assert field("fit")["enum"] == ["contain", "cover", "pad", "scale"]
    assert (field("scale_percent")["minimum"], field("scale_percent")["maximum"]) == (1, 100)
    assert (field("webp_quality")["minimum"], field("webp_quality")["maximum"]) == (60, 95)


def test_importar_o_modelo_v2_nao_carrega_o_pil() -> None:
    code = (
        "import sys, goblinshift.catalog.image_forge_v2;"
        "sys.exit(1 if any(m == 'PIL' or m.startswith('PIL.') for m in sys.modules) else 0)"
    )
    assert subprocess.run([sys.executable, "-c", code], check=False).returncode == 0  # noqa: S603
