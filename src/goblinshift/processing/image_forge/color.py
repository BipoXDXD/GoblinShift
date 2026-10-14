"""Perfis de cor por lista fechada (docs/GoblinShiftPlan.md, 1.2 e 2.3).

O perfil embutido é reconhecido pela impressão colorimétrica, nunca pela descrição textual, que
varia entre aparelhos: o LittleCMS leva um conjunto fixo de amostras do perfil candidato para
L*a*b* (D50, colorimétrica relativa), e o resultado é comparado com as impressões de sRGB e de
Display P3 abaixo. Primárias e secundárias distinguem os gamuts; os cinzas, a curva de
transferência.

As impressões foram calculadas uma vez, fora do código, a partir das especificações: primárias e
branco D65 do sRGB (IEC 61966-2-1) e do Display P3 (SMPTE EG 432-1, com a curva do sRGB, como a
Apple define), adaptação de Bradford para o D50 da PCS (ICC.1, anexo E) e L*a*b* da CIE 15.
"""

import io
from enum import StrEnum
from typing import NoReturn

from PIL import Image, ImageCms

from goblinshift.processing.image_forge.rejection import ImageRejected, RejectionCode

type Lab = tuple[float, float, float]

_SAMPLES: tuple[tuple[int, int, int], ...] = (
    (255, 0, 0),
    (0, 255, 0),
    (0, 0, 255),
    (0, 255, 255),
    (255, 0, 255),
    (255, 255, 0),
    (255, 255, 255),
    (64, 64, 64),
    (128, 128, 128),
    (192, 192, 192),
)
_SRGB_FINGERPRINT: tuple[Lab, ...] = (
    (54.29, 80.81, 69.89),
    (87.82, -79.27, 80.99),
    (29.57, 68.29, -112.03),
    (90.67, -50.66, -14.96),
    (60.17, 93.55, -60.50),
    (97.61, -15.75, 93.39),
    (100.00, 0.00, 0.00),
    (27.09, 0.00, 0.00),
    (53.59, 0.00, 0.00),
    (77.70, 0.00, 0.00),
)
_DISPLAY_P3_FINGERPRINT: tuple[Lab, ...] = (
    (56.21, 94.48, 98.89),
    (86.62, -106.54, 102.87),
    (31.01, 70.45, -115.59),
    (89.80, -68.48, -17.66),
    (62.32, 105.78, -61.53),
    (97.37, -17.43, 122.03),
    (100.00, 0.00, 0.00),
    (27.09, 0.00, 0.00),
    (53.59, 0.00, 0.00),
    (77.70, 0.00, 0.00),
)
# Por componente. O L*a*b* de 8 bits do Pillow tem passo de 0,39 em L* e de 1 em a* e b*; o resto
# da folga cobre o arredondamento s15Fixed16 dos perfis reais.
TOLERANCE = 1.5
_LAB_L_SCALE = 100 / 255
_LAB_AB_OFFSET = 128
_RGB_SPACE = "RGB"
# EXIF ColorSpace: 1 declara sRGB; 0xFFFF é "não calibrado", que o perfil ICC resolve.
EXIF_SRGB = 1
EXIF_UNCALIBRATED = 0xFFFF


class ColorProfile(StrEnum):
    """Como a cor da saída foi obtida; vai para `details["color_profile"]`."""

    SRGB = "srgb"
    DISPLAY_P3_CONVERTED = "display-p3-converted"
    ASSUMED_SRGB = "assumed-srgb"


class _Known(StrEnum):
    SRGB = "sRGB"
    DISPLAY_P3 = "Display P3"


def to_srgb(
    image: Image.Image, icc: bytes | None, exif_color_space: int | None, declares_srgb: bool
) -> tuple[Image.Image, ColorProfile]:
    """Imagem em sRGB e como isso foi obtido, ou recusa com `UNSUPPORTED_COLOR_PROFILE`.

    `declares_srgb` é o bloco `sRGB` do PNG. `image` já está em RGB, RGBA, L ou LA.
    """
    if exif_color_space not in (None, EXIF_SRGB, EXIF_UNCALIBRATED):
        _reject(f"EXIF ColorSpace {exif_color_space} fora da lista (sRGB ou perfil ICC)")
    declared_srgb = declares_srgb or exif_color_space == EXIF_SRGB
    if not icc:
        return image, ColorProfile.SRGB if declared_srgb else ColorProfile.ASSUMED_SRGB
    profile = _parse(icc)
    if image.mode not in ("RGB", "RGBA"):
        _reject(f"perfil de cor numa imagem {image.mode}; só perfis RGB em imagens coloridas")
    match _recognize(profile):
        case _Known.SRGB:
            return image, ColorProfile.SRGB
        case _Known.DISPLAY_P3 if declared_srgb:
            _reject("metadados conflitantes: a imagem declara sRGB, mas o perfil é Display P3")
        case _Known.DISPLAY_P3:
            return _convert_to_srgb(image, profile), ColorProfile.DISPLAY_P3_CONVERTED


def _parse(icc: bytes) -> ImageCms.ImageCmsProfile:
    try:
        profile = ImageCms.ImageCmsProfile(io.BytesIO(icc))
    except OSError, ImageCms.PyCMSError:
        _reject("perfil ICC ilegível")
    if profile.profile.xcolor_space.strip() != _RGB_SPACE:
        _reject(f"perfil ICC de espaço {profile.profile.xcolor_space.strip()}, não RGB")
    return profile


def _recognize(profile: ImageCms.ImageCmsProfile) -> _Known:
    fingerprint = _fingerprint(profile)
    if _matches(fingerprint, _SRGB_FINGERPRINT):
        return _Known.SRGB
    if _matches(fingerprint, _DISPLAY_P3_FINGERPRINT):
        return _Known.DISPLAY_P3
    _reject("perfil ICC fora da lista (só sRGB e Display P3)")


def _fingerprint(profile: ImageCms.ImageCmsProfile) -> list[Lab]:
    samples = Image.new("RGB", (len(_SAMPLES), 1))
    for x, rgb in enumerate(_SAMPLES):
        samples.putpixel((x, 0), rgb)
    try:
        transform = ImageCms.buildTransform(
            profile,
            ImageCms.createProfile("LAB"),
            "RGB",
            "LAB",
            renderingIntent=ImageCms.Intent.RELATIVE_COLORIMETRIC,
        )
        lab = ImageCms.applyTransform(samples, transform)
    except ImageCms.PyCMSError:
        _reject("perfil ICC sem transformação para L*a*b*")
    if lab is None:  # só acontece com inPlace=True
        raise AssertionError("applyTransform sem inPlace devolve imagem")
    return [_decoded_lab(lab.getpixel((x, 0))) for x in range(len(_SAMPLES))]


def _decoded_lab(pixel: object) -> Lab:
    if not isinstance(pixel, tuple) or len(pixel) != len("Lab"):
        raise AssertionError(f"pixel L*a*b* inesperado: {pixel!r}")
    lightness, a, b = pixel
    return (lightness * _LAB_L_SCALE, a - _LAB_AB_OFFSET, b - _LAB_AB_OFFSET)


def _matches(measured: list[Lab], reference: tuple[Lab, ...]) -> bool:
    return all(
        abs(m - r) <= TOLERANCE
        for sample, expected in zip(measured, reference, strict=True)
        for m, r in zip(sample, expected, strict=True)
    )


def _convert_to_srgb(image: Image.Image, source: ImageCms.ImageCmsProfile) -> Image.Image:
    """Display P3 → sRGB, colorimétrica relativa; o alfa não passa pela transformação."""
    alpha = image.getchannel("A") if image.mode == "RGBA" else None
    rgb = image.convert("RGB") if alpha is not None else image
    converted = ImageCms.profileToProfile(
        rgb,
        source,
        ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")),
        renderingIntent=ImageCms.Intent.RELATIVE_COLORIMETRIC,
        outputMode="RGB",
    )
    if converted is None:  # só acontece com inPlace=True
        raise AssertionError("profileToProfile sem inPlace devolve imagem")
    if alpha is not None:
        converted.putalpha(alpha)
    return converted


def _reject(reason: str) -> NoReturn:
    raise ImageRejected(RejectionCode.UNSUPPORTED_COLOR_PROFILE, reason)
