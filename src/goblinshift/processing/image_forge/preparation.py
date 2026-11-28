"""Do arquivo à imagem pronta para redimensionar (docs/GoblinShiftPlan.md, 1.2).

Ordem: cabeçalho → validação → `load()` → orientação EXIF → modo → perfil de cor. Só as
chamadas do Pillow traduzem os erros dele em `ImageRejected`: a decodificação inteira (até a
orientação, com a leitura do EXIF e do ICC) e, na normalização de modo, o `convert()`, que é onde
um `tRNS` maior que a paleta estoura. O resto da normalização de modo e de cor fica fora, para um
bug nosso propagar em vez de virar imagem recusada. A gravação também fica fora, para um erro de
disco ou de teto de saída nunca virar rejeição silenciosa (1.1).

Os avisos do Pillow durante a decodificação viram erro e recusa: um EXIF corrompido, por
exemplo, só gera `UserWarning`, e seguir em frente seria adivinhar a orientação.
"""

import struct
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, NoReturn

from PIL import ExifTags, Image, UnidentifiedImageError

from goblinshift.processing.image_forge.color import ColorProfile, to_srgb
from goblinshift.processing.image_forge.inspection import (
    MPO,
    V1_FORMATS,
    Header,
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
    source_format: str  # "jpeg", "png", "mpo", "webp" ou "gif"
    orientation: int  # 1 a 8, a que foi aplicada
    color_profile: ColorProfile
    frames_discarded: int  # quadros do MPO além do principal


@dataclass(frozen=True)
class _Decoded:
    image: Image.Image  # já orientada, cópia independente do arquivo
    header: Header
    orientation: int
    icc: bytes | None
    exif_color_space: int | None
    declares_srgb: bool


def prepare(path: Path, limits: Limits, formats: tuple[str, ...] = V1_FORMATS) -> Prepared:
    """Imagem pronta para redimensionar, ou `ImageRejected`; só abre os `formats` da versão."""
    with path.open("rb") as source:
        try:
            with warnings.catch_warnings(action="error"):
                decoded = _decode(source, limits, formats)
        except _BOMB as error:
            raise ImageRejected(
                RejectionCode.IMAGE_TOO_LARGE, "imagem acima do limite de pixels"
            ) from error
        except UnidentifiedImageError as error:
            raise ImageRejected(
                RejectionCode.UNSUPPORTED_FORMAT, _unrecognized_reason(formats)
            ) from error
        except _DECODER_ERRORS as error:
            _reject_unreadable(error)
    srgb, color_profile = to_srgb(
        _normalized_mode(decoded.image),
        decoded.icc,
        decoded.exif_color_space,
        decoded.declares_srgb,
    )
    header = decoded.header
    return Prepared(
        image=srgb,
        source_format=header.image_format.lower(),
        orientation=decoded.orientation,
        color_profile=color_profile,
        frames_discarded=header.frames - 1 if header.image_format == MPO else 0,
    )


def _unrecognized_reason(formats: tuple[str, ...]) -> str:
    """Na v1, "não é um JPEG nem um PNG reconhecível"; nas outras, a lista da versão."""
    names = [_FORMAT_NAMES.get(name, name) for name in formats]
    listed = ", ".join(names[:-1]) + " nem um " + names[-1] if len(names) > 1 else names[0]
    return f"não é um {listed} reconhecível"


_FORMAT_NAMES = {"WEBP": "WebP"}


def _reject_unreadable(error: Exception) -> NoReturn:
    """Erro do Pillow vira recusa; `OSError` com errno é falha real de leitura e propaga."""
    if isinstance(error, OSError) and error.errno is not None:
        raise error
    raise ImageRejected(
        RejectionCode.UNSUPPORTED_FORMAT, "imagem corrompida ou ilegível"
    ) from error


def _decode(source: BinaryIO, limits: Limits, formats: tuple[str, ...]) -> _Decoded:
    prefix = header_prefix(source)
    with open_image(source, formats) as opened:
        header = read_header(opened, prefix)
        validate(header, limits)
        opened.seek(0)  # MPO: só o quadro principal
        opened.load()
        exif = opened.getexif()
        orientation = _orientation(exif)
        icc = opened.info.get("icc_profile")
        # Cópia antes de sair do `with`: fechar o arquivo do Pillow descarta a imagem aberta.
        return _Decoded(
            image=_oriented(opened, orientation),
            header=header,
            orientation=orientation,
            icc=icc if isinstance(icc, bytes) else None,
            exif_color_space=exif.get_ifd(ExifTags.IFD.Exif).get(ExifTags.Base.ColorSpace),
            declares_srgb="srgb" in opened.info,
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
            return _converted(image, "L")
        case "P":
            return _converted(image, "RGBA" if transparent else "RGB")
        case "RGB" if transparent:
            return _converted(image, "RGBA")
        case "L" if transparent:
            return _converted(image, "LA")
        case _:
            return image


def _converted(image: Image.Image, mode: str) -> Image.Image:
    try:
        return image.convert(mode)
    except _DECODER_ERRORS as error:
        _reject_unreadable(error)
