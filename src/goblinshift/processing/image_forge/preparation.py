"""Do arquivo à imagem pronta para redimensionar (docs/GoblinShiftPlan.md, 1.2).

Ordem: cabeçalho → validação → `load()` → orientação EXIF → modo → perfil de cor. Tudo o que o
decodificador pode recusar acontece aqui e vira `ImageRejected`; a gravação fica fora, para um
erro de disco ou de teto de saída nunca virar rejeição silenciosa (1.1).

Os avisos do Pillow durante a decodificação viram erro e recusa: um EXIF corrompido, por
exemplo, só gera `UserWarning`, e seguir em frente seria adivinhar a orientação.
"""

import struct
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from PIL import ExifTags, Image, UnidentifiedImageError

from goblinshift.processing.image_forge.color import ColorProfile, to_srgb
from goblinshift.processing.image_forge.inspection import (
    MPO,
    header_prefix,
    open_image,
    read_header,
    validate,
)
from goblinshift.processing.image_forge.limits import Limits
from goblinshift.processing.image_forge.rejection import ImageRejected, RejectionCode

# O Pillow sinaliza arquivo quebrado com qualquer um destes (2.3). O OSError só conta quando não
# traz errno: com errno é falha real de leitura, infraestrutura, e propaga.
_DECODER_ERRORS = (
    OSError,
    ValueError,
    SyntaxError,
    EOFError,
    struct.error,
    UserWarning,
)
_BOMB = (Image.DecompressionBombError, Image.DecompressionBombWarning)

NORMAL_ORIENTATION = 1
# Orientação EXIF → transposição que a desfaz (EXIF 2.32, tag 0x0112).
_TRANSPOSITIONS = {
    2: Image.Transpose.FLIP_LEFT_RIGHT,
    3: Image.Transpose.ROTATE_180,
    4: Image.Transpose.FLIP_TOP_BOTTOM,
    5: Image.Transpose.TRANSPOSE,
    6: Image.Transpose.ROTATE_270,
    7: Image.Transpose.TRANSVERSE,
    8: Image.Transpose.ROTATE_90,
}


@dataclass(frozen=True)
class Prepared:
    image: Image.Image  # orientada, em RGB, RGBA, L ou LA, em sRGB
    source_format: str  # "jpeg", "png" ou "mpo"
    orientation: int  # 1 a 8, a que foi aplicada
    color_profile: ColorProfile
    frames_discarded: int  # quadros do MPO além do principal


def prepare(path: Path, limits: Limits) -> Prepared:
    """Imagem pronta para redimensionar, ou `ImageRejected`."""
    with path.open("rb") as source:
        try:
            with warnings.catch_warnings(action="error"):
                return _prepare(source, limits)
        except _BOMB as error:
            raise ImageRejected(
                RejectionCode.IMAGE_TOO_LARGE, "imagem acima do limite de pixels"
            ) from error
        except UnidentifiedImageError as error:
            raise ImageRejected(
                RejectionCode.UNSUPPORTED_FORMAT, "não é um JPEG nem um PNG reconhecível"
            ) from error
        except _DECODER_ERRORS as error:
            if isinstance(error, OSError) and error.errno is not None:
                raise
            raise ImageRejected(
                RejectionCode.UNSUPPORTED_FORMAT, "imagem corrompida ou ilegível"
            ) from error


def _prepare(source: BinaryIO, limits: Limits) -> Prepared:
    prefix = header_prefix(source)
    with open_image(source) as opened:
        header = read_header(opened, prefix)
        validate(header, limits)
        opened.seek(0)  # MPO: só o quadro principal
        opened.load()
        exif = opened.getexif()
        orientation = _orientation(exif)
        icc = opened.info.get("icc_profile")
        declares_srgb = "srgb" in opened.info
        # Cópia antes de sair do `with`: fechar o arquivo do Pillow descarta a imagem aberta.
        image = _normalized_mode(_oriented(opened, orientation))
    srgb, color_profile = to_srgb(
        image,
        icc if isinstance(icc, bytes) else None,
        exif.get_ifd(ExifTags.IFD.Exif).get(ExifTags.Base.ColorSpace),
        declares_srgb,
    )
    return Prepared(
        image=srgb,
        source_format=header.image_format.lower(),
        orientation=orientation,
        color_profile=color_profile,
        frames_discarded=header.frames - 1 if header.image_format == MPO else 0,
    )


def _orientation(exif: Image.Exif) -> int:
    value = exif.get(ExifTags.Base.Orientation, NORMAL_ORIENTATION)
    if value != NORMAL_ORIENTATION and value not in _TRANSPOSITIONS:
        raise ImageRejected(
            RejectionCode.UNSUPPORTED_FORMAT, f"orientação EXIF inválida: {value!r:.40}"
        )
    return int(value)


def _oriented(image: Image.Image, orientation: int) -> Image.Image:
    transposition = _TRANSPOSITIONS.get(orientation)
    return image.copy() if transposition is None else image.transpose(transposition)


def _normalized_mode(image: Image.Image) -> Image.Image:
    """RGB, RGBA, L ou LA (1.2): paleta conforme a transparência, 1 bit como cinza.

    A transparência por cor-chave (`tRNS` em PNG RGB ou cinza) também vira alfa: sem isso, a
    limpeza dos metadados a descartaria em silêncio.
    """
    transparent = "transparency" in image.info
    match image.mode:
        case "1":
            return image.convert("L")
        case "P":
            return image.convert("RGBA" if transparent else "RGB")
        case "RGB" if transparent:
            return image.convert("RGBA")
        case "L" if transparent:
            return image.convert("LA")
        case _:
            return image
