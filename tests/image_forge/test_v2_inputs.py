"""Entrada WebP e GIF estáticos: só pelos formatos da v2; a v1 continua recusando os dois."""

from pathlib import Path

import pytest
from PIL import Image

from goblinshift.processing.image_forge.color import ColorProfile
from goblinshift.processing.image_forge.inspection import V1_FORMATS, V2_FORMATS
from goblinshift.processing.image_forge.limits import Limits
from goblinshift.processing.image_forge.preparation import prepare
from goblinshift.processing.image_forge.rejection import ImageRejected, RejectionCode
from tests.image_forge import icc, media

ORANGE = (255, 128, 0)


def _write(tmp_path: Path, name: str, data: bytes) -> Path:
    path = tmp_path / name
    path.write_bytes(data)
    return path


def palette_gif(transparent_index: int | None) -> bytes:
    image = Image.new("P", (4, 4), 0)
    image.putpalette([255, 0, 0, 0, 0, 255] + [0] * (256 * 3 - 6))
    image.putpixel((0, 0), 1)
    if transparent_index is None:
        return media.gif(image)
    return media.gif(image, transparency=transparent_index)


STILL = [
    ("a.webp", media.webp(media.solid("RGB", (6, 4), ORANGE))),
    ("a.gif", media.gif(media.solid("RGB", (6, 4), ORANGE))),
]


def test_v1_e_o_default_e_tem_so_jpeg_e_png() -> None:
    assert V1_FORMATS == ("JPEG", "PNG")
    assert V2_FORMATS == ("JPEG", "PNG", "WEBP", "GIF")


@pytest.mark.parametrize(("name", "data"), STILL, ids=["webp", "gif"])
def test_v1_continua_recusando_webp_e_gif(tmp_path: Path, name: str, data: bytes) -> None:
    with pytest.raises(ImageRejected) as caught:
        prepare(_write(tmp_path, name, data), Limits())
    assert caught.value.code is RejectionCode.UNSUPPORTED_FORMAT


@pytest.mark.parametrize(("name", "data"), STILL, ids=["webp", "gif"])
def test_webp_e_gif_estaticos_entram_pela_v2(tmp_path: Path, name: str, data: bytes) -> None:
    prepared = prepare(_write(tmp_path, name, data), Limits(), V2_FORMATS)
    assert prepared.source_format == name.removeprefix("a.")
    assert prepared.image.mode in {"RGB", "RGBA", "L", "LA"}
    assert prepared.image.size == (6, 4)
    assert prepared.frames_discarded == 0


@pytest.mark.parametrize(
    ("data", "name", "label"),
    [
        (media.animated_gif(2), "a.png", "GIF"),
        (media.animated_webp(2), "a.jpg", "WEBP"),
        (media.animated_gif(3), "a.gif", "GIF"),
    ],
    ids=["gif-como-png", "webp-como-jpg", "gif"],
)
def test_gif_e_webp_animados_sao_recusados(
    tmp_path: Path, data: bytes, name: str, label: str
) -> None:
    with pytest.raises(ImageRejected) as caught:
        prepare(_write(tmp_path, name, data), Limits(), V2_FORMATS)
    assert caught.value.code is RejectionCode.UNSUPPORTED_FORMAT
    assert caught.value.reason == f"{label} animado não é aceito"


def test_png_animado_continua_com_o_motivo_de_sempre_na_v2(tmp_path: Path) -> None:
    frames = [media.solid("RGB", (4, 4), (255, 0, 0)), media.solid("RGB", (4, 4), (0, 0, 255))]
    with pytest.raises(ImageRejected) as caught:
        prepare(_write(tmp_path, "a.png", media.apng(frames)), Limits(), V2_FORMATS)
    assert caught.value.reason == "PNG animado não é aceito"


def test_gif_com_transparencia_sai_com_alfa(tmp_path: Path) -> None:
    prepared = prepare(_write(tmp_path, "a.gif", palette_gif(0)), Limits(), V2_FORMATS)
    assert prepared.image.mode in {"RGBA", "LA"}
    assert prepared.image.getpixel((1, 1))[-1] == 0  # type: ignore[index]
    assert prepared.image.getpixel((0, 0)) == (0, 0, 255, 255)


def test_gif_sem_transparencia_sai_opaco(tmp_path: Path) -> None:
    prepared = prepare(_write(tmp_path, "a.gif", palette_gif(None)), Limits(), V2_FORMATS)
    assert prepared.image.mode == "RGB"


def test_webp_em_display_p3_e_convertido_como_o_jpeg(tmp_path: Path) -> None:
    data = media.webp(
        media.solid("RGB", (4, 4), ORANGE),
        lossless=True,
        icc_profile=icc.rgb_profile(icc.DISPLAY_P3, "Display P3"),
    )
    prepared = prepare(_write(tmp_path, "p3.webp", data), Limits(), V2_FORMATS)
    assert prepared.color_profile is ColorProfile.DISPLAY_P3_CONVERTED


def test_webp_e_gif_tambem_respeitam_o_teto_de_pixels(tmp_path: Path) -> None:
    data = media.webp(media.solid("RGB", (40, 30), ORANGE))
    with pytest.raises(ImageRejected) as caught:
        prepare(_write(tmp_path, "a.webp", data), Limits(max_pixels=1000), V2_FORMATS)
    assert caught.value.code is RejectionCode.IMAGE_TOO_LARGE
