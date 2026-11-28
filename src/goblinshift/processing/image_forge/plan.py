"""O plano interno do Image Forge: tudo o que o pipeline precisa, sem o PIL.

`forge` (em `recipe.py`) roda um `ForgePlan`; v1 e v2 são dois adapters finos que montam o plano
a partir dos parâmetros de cada versão. A variação entre versões é dado (formatos aceitos, modo de
ajuste, codificação), não algoritmo: por isso um plano congelado e `match` sobre os enums, sem
hierarquia de classes. Converter `Params` em `ParamsV2` não serviria: a v1 precisa continuar
recusando WebP e GIF, e essa restrição não é parâmetro do usuário.
"""

from dataclasses import dataclass

from goblinshift.processing.image_forge.formats import V1_FORMATS, V2_FORMATS
from goblinshift.processing.image_forge.geometry import FitMode
from goblinshift.processing.image_forge.params import HexColor, Params
from goblinshift.processing.image_forge.params_v2 import (
    DEFAULT_SCALE_PERCENT,
    DEFAULT_WEBP_QUALITY,
    OutputFormatV2,
    ParamsV2,
)


@dataclass(frozen=True)
class ForgePlan:
    formats: tuple[str, ...]  # nomes do Pillow aceitos na entrada
    fit: FitMode
    box: tuple[int, int]
    scale_percent: int
    output: OutputFormatV2
    jpeg_quality: int
    jpeg_background: HexColor
    webp_quality: int
    webp_lossless: bool
    make_thumbnails: bool
    # A v1 está congelada (1.6): os `details` dela não ganham os campos do ajuste.
    fit_details: bool


def plan_from_v1(params: Params) -> ForgePlan:
    return ForgePlan(
        formats=V1_FORMATS,
        fit=FitMode.CONTAIN,
        box=(params.max_width, params.max_height),
        scale_percent=DEFAULT_SCALE_PERCENT,
        output=OutputFormatV2(params.output_format.value),
        jpeg_quality=params.jpeg_quality,
        jpeg_background=params.jpeg_background,
        webp_quality=DEFAULT_WEBP_QUALITY,
        webp_lossless=False,
        make_thumbnails=params.make_thumbnails,
        fit_details=False,
    )


def plan_from_v2(params: ParamsV2) -> ForgePlan:
    return ForgePlan(
        formats=V2_FORMATS,
        fit=params.fit,
        box=(params.max_width, params.max_height),
        scale_percent=params.scale_percent,
        output=params.output_format,
        jpeg_quality=params.jpeg_quality,
        jpeg_background=params.jpeg_background,
        webp_quality=params.webp_quality,
        webp_lossless=params.webp_lossless,
        make_thumbnails=params.make_thumbnails,
        fit_details=True,
    )
