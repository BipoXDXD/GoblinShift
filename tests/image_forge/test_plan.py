"""`ParamsV2` e `ForgePlan`: a v2 nasce válida na combinação, e cada versão monta o seu plano."""

from collections.abc import Mapping

import pytest

from goblinshift.processing.image_forge import (
    InvalidParametersError,
    OutputFormat,
    OutputFormatV2,
    Params,
    ParamsV2,
    params_v2_from_mapping,
)
from goblinshift.processing.image_forge.geometry import FitMode
from goblinshift.processing.image_forge.inspection import V1_FORMATS, V2_FORMATS
from goblinshift.processing.image_forge.plan import plan_from_v1, plan_from_v2


def test_plano_da_v1_aceita_so_jpeg_e_png_em_contain() -> None:
    plan = plan_from_v1(Params(output_format=OutputFormat.PNG))
    assert (plan.formats, plan.fit) == (V1_FORMATS, FitMode.CONTAIN)
    assert plan.output is OutputFormatV2.PNG
    assert not plan.fit_details


def test_plano_da_v2_aceita_webp_e_gif() -> None:
    plan = plan_from_v2(ParamsV2(fit=FitMode.COVER, max_width=1080, max_height=1080))
    assert (plan.formats, plan.fit, plan.box) == (V2_FORMATS, FitMode.COVER, (1080, 1080))
    assert plan.fit_details


def test_defaults_da_v2_sao_os_da_v1_mais_os_novos() -> None:
    assert params_v2_from_mapping({}) == ParamsV2()
    v2 = ParamsV2()
    assert (v2.fit, v2.scale_percent, v2.webp_quality, v2.webp_lossless) == (
        FitMode.CONTAIN,
        100,
        80,
        False,
    )
    v1 = Params()
    for name in ("max_width", "max_height", "jpeg_quality", "jpeg_background", "make_thumbnails"):
        assert getattr(v2, name) == getattr(v1, name)
    assert v2.output_format.value == v1.output_format.value


@pytest.mark.parametrize(
    ("raw", "field"),
    [
        ({"scale_percent": 50}, "scale_percent"),
        ({"fit": "cover", "scale_percent": 50}, "scale_percent"),
        ({"fit": "scale", "max_width": 800}, "max_width"),
        ({"fit": "scale", "max_height": 800}, "max_height"),
        ({"output_format": "webp", "jpeg_quality": 90}, "jpeg_quality"),
        ({"output_format": "png", "jpeg_background": "#000000"}, "jpeg_background"),
        ({"output_format": "jpeg", "webp_lossless": True}, "webp_lossless"),
        ({"output_format": "png", "webp_quality": 90}, "webp_quality"),
        ({"output_format": "webp", "webp_lossless": True, "webp_quality": 90}, "webp_quality"),
    ],
)
def test_combinacao_invalida_da_v2_e_recusada(raw: Mapping[str, object], field: str) -> None:
    with pytest.raises(InvalidParametersError, match=field):
        params_v2_from_mapping(raw)


@pytest.mark.parametrize(
    "raw",
    [
        {"fit": "scale", "scale_percent": 50},
        {"output_format": "webp", "webp_quality": 90},
        {"output_format": "webp", "webp_lossless": True},
        {"output_format": "preserve", "webp_quality": 60, "jpeg_quality": 60},
        {"fit": "pad", "max_width": 400, "max_height": 400, "jpeg_background": "#FF0000"},
        # Forma resolvida pelo catálogo: todos os defaults presentes continuam válidos.
        {
            "output_format": "png",
            "jpeg_quality": 85,
            "jpeg_background": "#FFFFFF",
            "webp_quality": 80,
            "webp_lossless": False,
            "scale_percent": 100,
        },
    ],
)
def test_combinacao_valida_da_v2_e_aceita(raw: Mapping[str, object]) -> None:
    params_v2_from_mapping(raw)


@pytest.mark.parametrize(
    ("raw", "field"),
    [
        ({"fit": "zoom"}, "fit"),
        ({"output_format": "gif"}, "output_format"),
        ({"scale_percent": 0, "fit": "scale"}, "scale_percent"),
        ({"scale_percent": 101, "fit": "scale"}, "scale_percent"),
        ({"webp_quality": 59, "output_format": "webp"}, "webp_quality"),
        ({"webp_quality": 96, "output_format": "webp"}, "webp_quality"),
        ({"webp_lossless": 1}, "webp_lossless"),
        ({"scale_percent": True}, "scale_percent"),
        ({"quality": 80}, "quality"),
    ],
)
def test_valor_fora_do_tipo_ou_da_faixa_e_recusado(raw: Mapping[str, object], field: str) -> None:
    with pytest.raises(InvalidParametersError, match=field):
        params_v2_from_mapping(raw)
