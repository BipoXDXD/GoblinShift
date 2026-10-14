"""IHDR lido sem o PIL: o Pillow rebaixa PNG colorido de 16 bits para 8 sem avisar (2.3)."""

import struct

import pytest

from goblinshift.processing.image_forge.header import PNG_HEADER_BYTES, PngHeader, parse_png_header

SIGNATURE = b"\x89PNG\r\n\x1a\n"


def ihdr(width: int, height: int, depth: int, color_type: int) -> bytes:
    data = struct.pack(">IIBBBBB", width, height, depth, color_type, 0, 0, 0)
    return SIGNATURE + struct.pack(">I", len(data)) + b"IHDR" + data + b"\0\0\0\0"


@pytest.mark.parametrize(
    ("depth", "color_type"),
    [(8, 2), (16, 2), (16, 6), (16, 0), (16, 4), (1, 0), (4, 3), (8, 6)],
)
def test_le_dimensoes_profundidade_e_tipo_de_cor(depth: int, color_type: int) -> None:
    assert parse_png_header(ihdr(640, 480, depth, color_type)) == PngHeader(
        width=640, height=480, bit_depth=depth, color_type=color_type
    )


def test_basta_ler_png_header_bytes_do_inicio() -> None:
    assert parse_png_header(ihdr(1, 1, 8, 2)[:PNG_HEADER_BYTES]) is not None


@pytest.mark.parametrize(
    "prefix",
    [
        b"",
        b"\xff\xd8\xff\xe0" + b"\0" * 40,  # JPEG
        SIGNATURE,  # só a assinatura
        ihdr(1, 1, 8, 2)[: PNG_HEADER_BYTES - 1],  # IHDR cortado
        ihdr(1, 1, 8, 2).replace(b"IHDR", b"IDAT"),  # primeiro bloco não é o IHDR
        SIGNATURE + struct.pack(">I", 12) + b"IHDR" + b"\0" * 17,  # tamanho do IHDR errado
    ],
)
def test_sem_ihdr_valido_no_inicio_nao_ha_cabecalho(prefix: bytes) -> None:
    assert parse_png_header(prefix) is None
