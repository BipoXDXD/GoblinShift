"""Modos de imagem e transparência (1.2): o que entra, o que sai e o fundo do JPEG."""

import pytest
from PIL import Image

from goblinshift.processing.image_forge import HexColor, OutputFormat, Params
from tests.image_forge import media
from tests.image_forge.conftest import Workspace, only_ok

JPEG_TOLERANCE = 6


def palette_image(transparent_index: int | None) -> Image.Image:
    image = Image.new("P", (4, 4), 0)
    image.putpalette([255, 0, 0, 0, 0, 255] + [0] * (256 * 3 - 6))
    image.putpixel((0, 0), 1)
    if transparent_index is not None:
        image.info["transparency"] = transparent_index
    return image


def png_of(mode: str) -> bytes:
    match mode:
        case "P":
            return media.png(palette_image(None))
        case "P+tRNS":
            return media.png(palette_image(0))
        case "RGB+tRNS":
            return media.png(media.solid("RGB", (4, 4), (9, 9, 9)), transparency=(9, 9, 9))
        case "L+tRNS":
            return media.png(media.solid("L", (4, 4), 9), transparency=9)
        case "1":
            return media.png(media.solid("1", (4, 4), 1))
        case "LA":
            return media.png(media.solid("LA", (4, 4), (200, 100)))
        case "RGBA":
            return media.png(media.solid("RGBA", (4, 4), (10, 20, 30, 100)))
        case _:
            return media.png(media.solid(mode, (4, 4), 0))


@pytest.mark.parametrize(
    ("mode", "png_mode", "jpeg_mode"),
    [
        ("RGB", "RGB", "RGB"),
        ("RGBA", "RGBA", "RGB"),
        ("L", "L", "L"),
        ("LA", "LA", "RGB"),
        ("P", "RGB", "RGB"),
        ("P+tRNS", "RGBA", "RGB"),
        ("RGB+tRNS", "RGBA", "RGB"),
        ("L+tRNS", "LA", "RGB"),
        ("1", "L", "L"),
    ],
)
@pytest.mark.parametrize("output_format", [OutputFormat.PNG, OutputFormat.JPEG])
def test_modo_de_saida(
    work: Workspace, mode: str, png_mode: str, jpeg_mode: str, output_format: OutputFormat
) -> None:
    work.add("a.png", png_of(mode))

    only_ok(work.run(Params(output_format=output_format)))

    extension = "png" if output_format is OutputFormat.PNG else "jpg"
    expected = png_mode if output_format is OutputFormat.PNG else jpeg_mode
    assert work.open_output(f"images/a.{extension}").mode == expected


def test_pixel_transparente_continua_transparente_no_png(work: Workspace) -> None:
    image = media.solid("RGBA", (4, 4), (0, 0, 0, 0))
    image.putpixel((3, 3), (10, 200, 10, 255))
    work.add("a.png", media.png(image))

    only_ok(work.run())

    output = work.open_output("images/a.png")
    assert output.getpixel((0, 0)) == (0, 0, 0, 0)
    assert output.getpixel((3, 3)) == (10, 200, 10, 255)


def test_paleta_com_trns_mantem_a_transparencia(work: Workspace) -> None:
    work.add("a.png", png_of("P+tRNS"))

    only_ok(work.run())

    output = work.open_output("images/a.png")
    alpha_of = output.getchannel("A")
    assert alpha_of.getpixel((1, 1)) == 0  # índice 0 é o transparente
    assert alpha_of.getpixel((0, 0)) == 255


def test_jpeg_compoe_transparencia_sobre_o_fundo_configurado(work: Workspace) -> None:
    image = media.solid("RGBA", (16, 16), (0, 0, 255, 128))  # azul meio transparente
    work.add("a.png", media.png(image))

    only_ok(work.run(Params(output_format=OutputFormat.JPEG, jpeg_background=HexColor(255, 0, 0))))

    red, green, blue = work.open_output("images/a.jpg").getpixel((8, 8))  # type: ignore[misc]
    # 128/255 de azul sobre vermelho: (127, 0, 128).
    assert abs(red - 127) <= JPEG_TOLERANCE
    assert green <= JPEG_TOLERANCE
    assert abs(blue - 128) <= JPEG_TOLERANCE


def test_fundo_padrao_e_branco_nunca_preto(work: Workspace) -> None:
    work.add("a.png", media.png(media.solid("RGBA", (16, 16), (0, 0, 0, 0))))

    only_ok(work.run(Params(output_format=OutputFormat.JPEG)))

    pixel = work.open_output("images/a.jpg").getpixel((8, 8))
    assert isinstance(pixel, tuple)
    assert all(channel >= 255 - JPEG_TOLERANCE for channel in pixel)


def test_cor_chave_do_trns_vira_fundo_no_jpeg(work: Workspace) -> None:
    work.add("a.png", png_of("RGB+tRNS"))  # toda a imagem tem a cor transparente

    only_ok(work.run(Params(output_format=OutputFormat.JPEG)))

    pixel = work.open_output("images/a.jpg").getpixel((2, 2))
    assert isinstance(pixel, tuple)
    assert all(channel >= 255 - JPEG_TOLERANCE for channel in pixel)
