"""Metadados removidos (1.2): cada saída é reaberta e os bytes são varridos.

Não é anonimização: só confirma que EXIF (com GPS), XMP, ICC e textos não chegam à saída.
"""

import zlib

from goblinshift.processing.image_forge import OutputFormat, Params
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
