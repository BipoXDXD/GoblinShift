"""Metadados removidos (1.2): cada saída é reaberta e os bytes são varridos.

Não é anonimização: só confirma que EXIF (com GPS), XMP, ICC e textos não chegam à saída.
"""

import io
import struct
import zlib
from typing import Any

import pytest
from PIL import Image

from goblinshift.processing.image_forge import OutputFormat, Params
from goblinshift.processing.image_forge.encoding import SaveFormat, encode
from goblinshift.processing.image_forge.params import WHITE
from tests.image_forge import icc, media
from tests.image_forge.conftest import Workspace, only_ok

APP0, APP1, APP2, APP13, COM = 0xE0, 0xE1, 0xE2, 0xED, 0xFE
SOI_TO_SOS_ALLOWED = {APP0, 0xDB, 0xC0, 0xC4, 0xDA}  # JFIF, tabelas, SOF0, Huffman, SOS
PNG_ALLOWED = {b"IHDR", b"IDAT", b"IEND"}
CANARY = "CANARIO-GPS-E-TEXTO"


def loaded_jpeg() -> bytes:
    return media.jpeg(
        media.solid("RGB", (16, 16), (90, 120, 150)),
        exif=media.exif(orientation=1, color_space=1, with_gps=True),
        icc_profile=icc.rgb_profile(icc.SRGB, "sRGB"),
        xmp=f"<x:xmpmeta>{CANARY}</x:xmpmeta>".encode(),
        comment=CANARY.encode(),
    )


def loaded_png() -> bytes:
    plain = media.png(
        media.solid("RGBA", (16, 16), (90, 120, 150, 200)),
        exif=media.exif(with_gps=True),
        icc_profile=icc.rgb_profile(icc.SRGB, "sRGB"),
    )
    texts = [
        media.chunk(b"tEXt", b"Comment\0" + CANARY.encode()),
        media.chunk(b"iTXt", b"XML:com.adobe.xmp\0\0\0\0\0" + CANARY.encode()),
        media.chunk(b"zTXt", b"Author\0\0" + zlib.compress(CANARY.encode())),
    ]
    return media.insert_after_ihdr(plain, b"".join(texts))


def assert_clean_jpeg(data: bytes) -> None:
    assert set(media.jpeg_markers(data)) <= SOI_TO_SOS_ALLOWED
    assert CANARY.encode() not in data
    assert b"Exif" not in data


def assert_clean_png(data: bytes) -> None:
    assert set(media.png_chunk_types(data)) <= PNG_ALLOWED
    assert CANARY.encode() not in data


def test_jpeg_sai_sem_exif_xmp_icc_nem_comentario(work: Workspace) -> None:
    work.add("a.jpg", loaded_jpeg())

    only_ok(work.run(Params(make_thumbnails=True)))

    for name in ("images/a.jpg", "thumbnails/a.jpg"):
        assert_clean_jpeg(work.output(name))
        reopened = work.open_output(name)
        assert not reopened.getexif()
        assert "icc_profile" not in reopened.info
        assert "xmp" not in reopened.info
        assert "comment" not in reopened.info


def test_png_sai_sem_textos_exif_nem_icc(work: Workspace) -> None:
    work.add("a.png", loaded_png())

    only_ok(work.run(Params(make_thumbnails=True)))

    for name in ("images/a.png", "thumbnails/a.png"):
        assert_clean_png(work.output(name))
        reopened = work.open_output(name)
        assert not reopened.getexif()
        assert reopened.info == {}


def test_jpeg_convertido_para_png_sai_limpo(work: Workspace) -> None:
    work.add("a.jpg", loaded_jpeg())
    only_ok(work.run(Params(output_format=OutputFormat.PNG)))
    assert_clean_png(work.output("images/a.png"))


def test_png_convertido_para_jpeg_sai_limpo(work: Workspace) -> None:
    work.add("b.png", loaded_png())
    only_ok(work.run(Params(output_format=OutputFormat.JPEG)))
    assert_clean_jpeg(work.output("images/b.jpg"))


# --- WebP da v2: gravado direto pelo `encode`, a partir de uma imagem cheia de metadados.


def riff_chunks(data: bytes) -> list[bytes]:
    """FourCCs do contêiner RIFF do WebP, na ordem."""
    assert data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    chunks, offset = [], 12
    while offset + 8 <= len(data):
        fourcc = data[offset : offset + 4]
        (size,) = struct.unpack_from("<I", data, offset + 4)
        chunks.append(fourcc)
        offset += 8 + size + (size & 1)
    return chunks


def webp_with_metadata() -> Image.Image:
    """WebP P3 com EXIF e XMP, reaberto: o `info` do Pillow traz os três."""
    data = media.webp(
        media.solid("RGB", (16, 16), (90, 120, 150)),
        lossless=True,
        exif=media.exif(orientation=1, with_gps=True),
        icc_profile=icc.rgb_profile(icc.DISPLAY_P3, "Display P3"),
        xmp=f"<x:xmpmeta>{CANARY}</x:xmpmeta>".encode(),
    )
    image = Image.open(io.BytesIO(data))
    image.load()
    assert {"exif", "icc_profile", "xmp"} <= set(image.info)
    return image


def encoded_webp(image: Image.Image, **options: Any) -> bytes:
    buffer = io.BytesIO()
    encode(image, SaveFormat.WEBP, buffer, jpeg_quality=85, jpeg_background=WHITE, **options)
    return buffer.getvalue()


@pytest.mark.parametrize("lossless", [False, True])
def test_webp_sai_sem_exif_icc_nem_xmp(lossless: bool) -> None:
    data = encoded_webp(webp_with_metadata(), webp_lossless=lossless)

    assert set(riff_chunks(data)) <= {b"VP8 ", b"VP8L", b"VP8X", b"ALPH"}
    assert CANARY.encode() not in data
    reopened = Image.open(io.BytesIO(data))
    assert not {"exif", "icc_profile", "xmp"} & set(reopened.info)


def test_webp_sem_perda_preserva_os_pixels() -> None:
    image = Image.new("RGBA", (8, 8))
    image.putdata([(x * 30, y * 30, 200, 40 + x + y) for y in range(8) for x in range(8)])

    reopened = Image.open(io.BytesIO(encoded_webp(image, webp_lossless=True)))

    assert reopened.convert("RGBA").tobytes() == image.tobytes()


def test_webp_de_cinza_com_alfa_sai_rgba() -> None:
    data = encoded_webp(media.solid("LA", (4, 4), (200, 100)), webp_lossless=True)
    reopened = Image.open(io.BytesIO(data))
    assert reopened.mode == "RGBA"
    assert reopened.getpixel((0, 0)) == (200, 200, 200, 100)


def test_webp_de_cinza_sai_rgb() -> None:
    data = encoded_webp(media.solid("L", (4, 4), 77), webp_lossless=True)
    reopened = Image.open(io.BytesIO(data))
    assert reopened.mode == "RGB"


def test_webp_e_deterministico() -> None:
    image = media.solid("RGB", (32, 32), (10, 200, 30))
    assert encoded_webp(image, webp_quality=70) == encoded_webp(image, webp_quality=70)
