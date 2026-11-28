"""Gravação explícita de JPEG, PNG e WebP sem metadados (docs/GoblinShiftPlan.md, 1.2 e 2.3).

O padrão do `save` do Pillow copia metadados de `Image.info` conforme o formato (comentário no
JPEG; perfil ICC e transparência no PNG). A imagem gravada é uma cópia com `info` vazio, e o
`save` recebe tudo de forma explícita: sem EXIF, ICC, XMP nem texto. Com os parâmetros fixos, a
saída é determinística para a mesma versão do Pillow. Sem ICC, a saída é sRGB por convenção.

O WebP só sai pela v2: a v1 nunca pede `SaveFormat.WEBP`.
"""

from enum import StrEnum
from typing import BinaryIO

from PIL import Image

from goblinshift.processing.image_forge.params import HexColor
from goblinshift.processing.image_forge.params_v2 import DEFAULT_WEBP_QUALITY

PNG_COMPRESS_LEVEL = 6
# Esforço do libwebp (0 a 6): 4 é o default dele, equilíbrio entre tamanho e tempo.
WEBP_METHOD = 4
_OPAQUE = 255
# O WebP só tem RGB e RGBA: o cinza vira cor, com e sem alfa.
_WEBP_MODES = {"L": "RGB", "LA": "RGBA"}


class SaveFormat(StrEnum):
    JPEG = "jpeg"
    PNG = "png"
    WEBP = "webp"

    @property
    def extension(self) -> str:
        match self:
            case SaveFormat.JPEG:
                return ".jpg"
            case SaveFormat.PNG:
                return ".png"
            case SaveFormat.WEBP:
                return ".webp"


def encode(
    image: Image.Image,
    save_format: SaveFormat,
    stream: BinaryIO,
    *,
    jpeg_quality: int,
    jpeg_background: HexColor,
    webp_quality: int = DEFAULT_WEBP_QUALITY,
    webp_lossless: bool = False,
) -> None:
    """Grava `image` (RGB, RGBA, L ou LA) no formato pedido."""
    match save_format:
        case SaveFormat.JPEG:
            _without_metadata(_flattened(image, jpeg_background)).save(
                stream,
                "JPEG",
                quality=jpeg_quality,
                subsampling="4:2:0",
                optimize=False,
                progressive=False,
                exif=b"",
            )
        case SaveFormat.PNG:
            _without_metadata(image).save(
                stream,
                "PNG",
                compress_level=PNG_COMPRESS_LEVEL,
                optimize=False,
                exif=b"",
                icc_profile=None,
                pnginfo=None,
            )
        case SaveFormat.WEBP:
            # `exact=False`: o libwebp pode trocar a cor de pixels 100% transparentes, que não
            # aparecem; com isso o arquivo fica menor e os pixels visíveis não mudam.
            _without_metadata(_webp_mode(image)).save(
                stream,
                "WEBP",
                quality=webp_quality,
                lossless=webp_lossless,
                method=WEBP_METHOD,
                exact=False,
                exif=b"",
                icc_profile=None,
                xmp=b"",
            )


def _webp_mode(image: Image.Image) -> Image.Image:
    mode = _WEBP_MODES.get(image.mode)
    return image if mode is None else image.convert(mode)


def _flattened(image: Image.Image, background: HexColor) -> Image.Image:
    """Compõe a transparência sobre o fundo configurado: nunca preto por acidente (1.2)."""
    if image.mode not in ("RGBA", "LA"):
        return image
    base = Image.new("RGBA", image.size, (*background.rgb, _OPAQUE))
    return Image.alpha_composite(base, image.convert("RGBA")).convert("RGB")


def _without_metadata(image: Image.Image) -> Image.Image:
    clean = image.copy()
    clean.info = {}
    return clean
