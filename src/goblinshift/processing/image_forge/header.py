"""Cabeçalho do PNG lido direto dos bytes, sem o PIL.

O Pillow abre PNG RGB e RGBA de 16 bits por canal como `RGB`/`RGBA` de 8 bits e trunca em
silêncio; só o cinza de 16 bits vira `I;16`. Checar o `mode` deixa passar o PNG colorido de 16
bits, então a profundidade sai do IHDR, que a especificação do PNG obriga a ser o primeiro bloco.
"""

import struct
from dataclasses import dataclass

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_IHDR = struct.Struct(">I4sIIBB")  # comprimento, tipo, largura, altura, profundidade, tipo de cor
_IHDR_DATA_BYTES = 13
# Assinatura + campos do IHDR que interessam; quem lê o arquivo passa pelo menos isto.
PNG_HEADER_BYTES = len(PNG_SIGNATURE) + _IHDR.size


@dataclass(frozen=True)
class PngHeader:
    width: int
    height: int
    bit_depth: int  # bits por amostra: 1, 2, 4, 8 ou 16
    color_type: int  # 0 cinza, 2 RGB, 3 paleta, 4 cinza com alfa, 6 RGBA


def parse_png_header(prefix: bytes) -> PngHeader | None:
    """IHDR do início de um PNG; `None` se os bytes não começam por um PNG com IHDR legível."""
    if not prefix.startswith(PNG_SIGNATURE) or len(prefix) < PNG_HEADER_BYTES:
        return None
    length, chunk_type, width, height, bit_depth, color_type = _IHDR.unpack_from(
        prefix, len(PNG_SIGNATURE)
    )
    if chunk_type != b"IHDR" or length != _IHDR_DATA_BYTES:
        return None
    return PngHeader(width, height, bit_depth, color_type)
