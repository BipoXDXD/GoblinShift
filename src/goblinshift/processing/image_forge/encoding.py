"""Gravação explícita de JPEG e PNG sem metadados (docs/GoblinShiftPlan.md, 1.2 e 2.3).

O padrão do `save` do Pillow copia metadados de `Image.info` conforme o formato (comentário no
JPEG; perfil ICC e transparência no PNG). A imagem gravada é uma cópia com `info` vazio, e o
`save` recebe tudo de forma explícita: sem EXIF, ICC, XMP nem texto. Com os parâmetros fixos, a
saída é determinística para a mesma versão do Pillow. Sem ICC, a saída é sRGB por convenção.
"""

from enum import StrEnum
from typing import BinaryIO

from PIL import Image

from goblinshift.processing.image_forge.params import HexColor

PNG_COMPRESS_LEVEL = 6
_OPAQUE = 255


class SaveFormat(StrEnum):
    JPEG = "jpeg"
    PNG = "png"

    @property
    def extension(self) -> str:
        return ".jpg" if self is SaveFormat.JPEG else ".png"


def encode(
    image: Image.Image,
    save_format: SaveFormat,
    stream: BinaryIO,
    *,
    jpeg_quality: int,
    jpeg_background: HexColor,
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
