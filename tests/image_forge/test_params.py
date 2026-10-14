"""Parâmetros do Image Forge (docs/GoblinShiftPlan.md, 1.2): nascem válidos."""

from collections.abc import Mapping

import pytest

from goblinshift.processing.image_forge import (
    HexColor,
    InvalidParametersError,
    OutputFormat,
    Params,
    params_from_mapping,
)


def test_defaults_de_1_2() -> None:
    assert params_from_mapping({}) == Params(
        max_width=1600,
        max_height=1600,
        output_format=OutputFormat.PRESERVE,
        jpeg_quality=85,
        jpeg_background=HexColor(255, 255, 255),
        make_thumbnails=False,
    )


@pytest.mark.parametrize("option", ["max_width", "max_height"])
@pytest.mark.parametrize("value", [16, 4096])
def test_lado_maximo_aceita_as_fronteiras(option: str, value: int) -> None:
    params = params_from_mapping({option: value})
    assert getattr(params, option) == value


@pytest.mark.parametrize("option", ["max_width", "max_height"])
@pytest.mark.parametrize("value", [15, 4097, 0, -1])
def test_lado_maximo_recusa_fora_da_faixa(option: str, value: int) -> None:
    with pytest.raises(InvalidParametersError, match=option):
        params_from_mapping({option: value})


@pytest.mark.parametrize("value", [60, 95])
def test_qualidade_aceita_as_fronteiras(value: int) -> None:
    assert params_from_mapping({"jpeg_quality": value}).jpeg_quality == value


@pytest.mark.parametrize("value", [59, 96])
def test_qualidade_recusa_fora_da_faixa(value: int) -> None:
    with pytest.raises(InvalidParametersError, match="jpeg_quality"):
        params_from_mapping({"jpeg_quality": value})


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ({"max_width": True}, "max_width"),
        ({"max_height": "1600"}, "max_height"),
        ({"jpeg_quality": 85.0}, "jpeg_quality"),
        ({"make_thumbnails": 1}, "make_thumbnails"),
        ({"make_thumbnails": "true"}, "make_thumbnails"),
        ({"output_format": "webp"}, "output_format"),
        ({"output_format": 1}, "output_format"),
        ({"jpeg_background": 0xFFFFFF}, "jpeg_background"),
    ],
)
def test_tipo_errado_e_recusado(raw: Mapping[str, object], expected: str) -> None:
    with pytest.raises(InvalidParametersError, match=expected):
        params_from_mapping(raw)


def test_campo_desconhecido_e_recusado() -> None:
    with pytest.raises(InvalidParametersError, match="quality"):
        params_from_mapping({"quality": 80})


@pytest.mark.parametrize("value", ["preserve", "jpeg", "png"])
def test_formatos_de_saida(value: str) -> None:
    assert params_from_mapping({"output_format": value}).output_format == OutputFormat(value)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("#FFFFFF", HexColor(255, 255, 255)),
        ("#ffffff", HexColor(255, 255, 255)),
        ("#000000", HexColor(0, 0, 0)),
        ("#FF8000", HexColor(255, 128, 0)),
    ],
)
def test_cor_de_fundo_hexadecimal(text: str, expected: HexColor) -> None:
    assert HexColor.parse(text) == expected


@pytest.mark.parametrize(
    "text",
    # O último usa dígitos arábico-índicos, que `int(..., 16)` aceitaria sem o padrão ASCII.
    ["#FFF", "FFFFFF", "#GGGGGG", "#FFFFFFF", "", "#FF FFF", "#" + "\u0660" * 6],
)
def test_cor_de_fundo_fora_do_formato_e_recusada(text: str) -> None:
    with pytest.raises(InvalidParametersError, match="jpeg_background"):
        HexColor.parse(text)


def test_cor_de_fundo_e_escrita_em_maiusculas() -> None:
    assert str(HexColor.parse("#a1b2c3")) == "#A1B2C3"


@pytest.mark.parametrize("component", [-1, 256])
def test_cor_com_componente_fora_de_um_byte_e_recusada(component: int) -> None:
    with pytest.raises(InvalidParametersError, match="jpeg_background"):
        HexColor(component, 0, 0)


def test_params_construido_direto_tambem_nasce_valido() -> None:
    with pytest.raises(InvalidParametersError, match="max_width"):
        Params(max_width=4097)


@pytest.mark.parametrize("option", [{"jpeg_quality": 90}, {"jpeg_background": HexColor(0, 0, 0)}])
def test_opcao_de_jpeg_diferente_do_default_com_saida_png_e_recusada(
    option: dict[str, object],
) -> None:
    with pytest.raises(InvalidParametersError, match="só vale para saídas JPEG"):
        Params(output_format=OutputFormat.PNG, **option)  # type: ignore[arg-type]


@pytest.mark.parametrize("output_format", ["preserve", "jpeg"])
def test_opcao_de_jpeg_vale_quando_pode_haver_saida_jpeg(output_format: str) -> None:
    params = params_from_mapping(
        {"output_format": output_format, "jpeg_quality": 90, "jpeg_background": "#000000"}
    )
    assert (params.jpeg_quality, params.jpeg_background) == (90, HexColor(0, 0, 0))


def test_saida_png_aceita_as_opcoes_de_jpeg_no_default() -> None:
    # A forma resolvida pelo catálogo sempre traz os defaults: repetir uma execução PNG com os
    # parâmetros registrados não pode ser recusado.
    params = params_from_mapping(
        {"output_format": "png", "jpeg_quality": 85, "jpeg_background": "#ffffff"}
    )
    assert params.output_format is OutputFormat.PNG
