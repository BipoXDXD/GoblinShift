"""Parâmetros do image-forge v1 como modelo Pydantic estrito do catálogo (1.2 e 1.6)."""

import subprocess
import sys
from typing import Any

import pytest
from pydantic import ValidationError

from goblinshift.catalog.image_forge_v1 import ImageForgeV1Parameters
from goblinshift.processing.image_forge import HexColor, OutputFormat, Params

DEFAULTS = {
    "jpeg_background": "#FFFFFF",
    "jpeg_quality": 85,
    "make_thumbnails": False,
    "max_height": 1600,
    "max_width": 1600,
    "output_format": "preserve",
}


def resolved(raw: dict[str, Any]) -> dict[str, Any]:
    return ImageForgeV1Parameters.model_validate(raw).model_dump(mode="json")


def test_defaults_resolvidos_de_1_2() -> None:
    assert resolved({}) == DEFAULTS


def test_converte_para_params_da_biblioteca() -> None:
    model = ImageForgeV1Parameters.model_validate(
        {"max_width": 800, "output_format": "jpeg", "jpeg_background": "#ff0000"}
    )
    assert model.to_params() == Params(
        max_width=800, output_format=OutputFormat.JPEG, jpeg_background=HexColor(255, 0, 0)
    )


def test_cor_de_fundo_sai_canonica_em_maiusculas() -> None:
    assert resolved({"jpeg_background": "#a1b2c3"})["jpeg_background"] == "#A1B2C3"


@pytest.mark.parametrize(
    ("raw", "location"),
    [
        ({"max_width": 15}, "max_width"),
        ({"max_width": 4097}, "max_width"),
        ({"max_height": 15}, "max_height"),
        ({"max_height": 4097}, "max_height"),
        ({"jpeg_quality": 59}, "jpeg_quality"),
        ({"jpeg_quality": 96}, "jpeg_quality"),
        ({"max_width": True}, "max_width"),
        ({"max_width": "1600"}, "max_width"),
        ({"jpeg_quality": 85.0}, "jpeg_quality"),
        ({"make_thumbnails": "true"}, "make_thumbnails"),
        ({"make_thumbnails": 1}, "make_thumbnails"),
        ({"output_format": "webp"}, "output_format"),
        ({"jpeg_background": "#FFF"}, "jpeg_background"),
        ({"jpeg_background": "FFFFFF"}, "jpeg_background"),
        ({"jpeg_background": "#GGGGGG"}, "jpeg_background"),
        ({"quality": 80}, "quality"),
    ],
)
def test_valor_invalido_aponta_o_campo(raw: dict[str, Any], location: str) -> None:
    with pytest.raises(ValidationError) as caught:
        ImageForgeV1Parameters.model_validate(raw)
    assert [error["loc"][0] for error in caught.value.errors()] == [location]


@pytest.mark.parametrize(
    "raw",
    [
        {"max_width": 16, "max_height": 4096, "jpeg_quality": 60},
        {"max_width": 4096, "max_height": 16, "jpeg_quality": 95},
    ],
)
def test_fronteiras_aceitas(raw: dict[str, Any]) -> None:
    assert resolved(raw) == {**DEFAULTS, **raw}


@pytest.mark.parametrize("option", [{"jpeg_quality": 90}, {"jpeg_background": "#000000"}])
def test_opcao_de_jpeg_fora_do_default_com_saida_png_e_recusada(option: dict[str, Any]) -> None:
    with pytest.raises(ValidationError, match="só vale para saídas JPEG"):
        ImageForgeV1Parameters.model_validate({"output_format": "png", **option})


def test_saida_png_sem_opcoes_de_jpeg_mantem_os_defaults_resolvidos() -> None:
    assert resolved({"output_format": "png"}) == {**DEFAULTS, "output_format": "png"}


def test_parametros_resolvidos_de_uma_execucao_png_sao_aceitos_de_novo() -> None:
    first = resolved({"output_format": "png"})
    assert resolved(first) == first


def test_schema_publica_faixas_enum_e_padrao_da_cor() -> None:
    schema = ImageForgeV1Parameters.model_json_schema()

    def field(name: str) -> dict[str, Any]:
        node: dict[str, Any] = schema["properties"][name]
        if "$ref" in node:
            node = {**schema["$defs"][node["$ref"].removeprefix("#/$defs/")], **node}
        return node

    assert (field("max_width")["minimum"], field("max_width")["maximum"]) == (16, 4096)
    assert (field("max_height")["minimum"], field("max_height")["maximum"]) == (16, 4096)
    assert (field("jpeg_quality")["minimum"], field("jpeg_quality")["maximum"]) == (60, 95)
    assert field("output_format")["enum"] == ["preserve", "jpeg", "png"]
    assert field("jpeg_background")["pattern"] == "^#[0-9A-Fa-f]{6}$"


def test_importar_o_modelo_nao_carrega_o_pil() -> None:
    code = (
        "import sys, goblinshift.catalog.image_forge_v1;"
        "sys.exit(1 if any(m == 'PIL' or m.startswith('PIL.') for m in sys.modules) else 0)"
    )
    assert subprocess.run([sys.executable, "-c", code], check=False).returncode == 0  # noqa: S603
