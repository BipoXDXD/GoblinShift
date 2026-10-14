"""Abertura restrita e validação antes de `load()` (docs/GoblinShiftPlan.md, 1.2, 1.7 e 2.3).

`Image.open` é preguiçoso: lê só o cabeçalho. Tudo o que limita custo (pixels, eixos, quadros,
profundidade, modo) é conferido aqui, antes de qualquer decodificação. O `MAX_IMAGE_PIXELS` do
Pillow é só a segunda barreira (`configure_pillow`): ele avisa acima de 1x e só levanta erro
acima de 2x o teto.
"""

import warnings
from dataclasses import dataclass
from typing import BinaryIO

from PIL import Image

from goblinshift.processing.image_forge.header import (
    PNG_HEADER_BYTES,
    PngHeader,
    parse_png_header,
)
from goblinshift.processing.image_forge.limits import Limits
from goblinshift.processing.image_forge.rejection import ImageRejected, RejectionCode

# Limita os decodificadores que o Pillow tenta. O MPO não entra na lista: ele vem pela fábrica do
# JPEG, que devolve um MpoImageFile quando o arquivo tem o índice de várias imagens.
ACCEPTED_FORMATS = ("JPEG", "PNG")
MPO = "MPO"
EIGHT_BIT_MODES = frozenset({"1", "L", "LA", "P", "RGB", "RGBA"})
# Modos que o Pillow usa para amostras de 16 ou 32 bits.
DEEP_MODES = frozenset({"I;16", "I;16B", "I;16L", "I;16N", "I", "F"})
EIGHT_BITS = 8


@dataclass(frozen=True)
class Header:
    """O que o Pillow e o IHDR dizem antes de decodificar."""

    image_format: str  # "JPEG", "PNG" ou "MPO"
    mode: str
    width: int
    height: int
    frames: int
    png: PngHeader | None  # só no PNG


def configure_pillow(limits: Limits | None = None) -> None:
    """Segunda barreira, global ao processo: chamada uma vez pelo `main` do executor."""
    Image.MAX_IMAGE_PIXELS = (limits or Limits()).max_pixels
    warnings.simplefilter("error", Image.DecompressionBombWarning)


def open_image(source: BinaryIO) -> Image.Image:
    """Abre sem decodificar, só com os formatos aceitos."""
    return Image.open(source, formats=ACCEPTED_FORMATS)


def read_header(image: Image.Image, prefix: bytes) -> Header:
    return Header(
        image_format=image.format or "",
        mode=image.mode,
        width=image.width,
        height=image.height,
        frames=getattr(image, "n_frames", 1),
        png=parse_png_header(prefix),
    )


def header_prefix(source: BinaryIO) -> bytes:
    """Bytes iniciais para o IHDR; devolve o arquivo à posição 0 para o Pillow."""
    prefix = source.read(PNG_HEADER_BYTES)
    source.seek(0)
    return prefix


def validate(header: Header, limits: Limits) -> None:
    """Recusa, sem decodificar, o que 1.2 e 1.7 não aceitam."""
    _check_size(header, limits)
    _check_frames(header)
    _check_depth(header)
    if header.mode not in EIGHT_BIT_MODES:
        raise ImageRejected(
            RejectionCode.UNSUPPORTED_FORMAT,
            f"modo de imagem {header.mode} não suportado (só RGB, RGBA, cinza e paleta)",
        )


def _check_size(header: Header, limits: Limits) -> None:
    if max(header.width, header.height) > limits.max_axis:
        raise ImageRejected(
            RejectionCode.IMAGE_TOO_LARGE,
            f"{header.width}x{header.height} px passa de {limits.max_axis} px por eixo",
        )
    if header.width * header.height > limits.max_pixels:
        raise ImageRejected(
            RejectionCode.IMAGE_TOO_LARGE,
            f"{header.width}x{header.height} px passa de {limits.max_pixels} pixels",
        )


def _check_frames(header: Header) -> None:
    if header.image_format == "PNG" and header.frames > 1:
        raise ImageRejected(RejectionCode.UNSUPPORTED_FORMAT, "PNG animado não é aceito")


def _check_depth(header: Header) -> None:
    if header.image_format == "PNG":
        if header.png is None:
            raise ImageRejected(RejectionCode.UNSUPPORTED_FORMAT, "PNG sem cabeçalho IHDR legível")
        if header.png.bit_depth > EIGHT_BITS:
            raise ImageRejected(
                RejectionCode.UNSUPPORTED_BIT_DEPTH,
                f"PNG de {header.png.bit_depth} bits por amostra; só até 8 bits",
            )
    if header.mode in DEEP_MODES:
        raise ImageRejected(
            RejectionCode.UNSUPPORTED_BIT_DEPTH, f"modo {header.mode} tem mais de 8 bits"
        )
