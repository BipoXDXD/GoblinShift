"""Image Forge v2 de ponta a ponta: ajustes, WebP e GIF sobre o mesmo pipeline da v1."""

from collections.abc import Callable

import pytest
from PIL import Image

from goblinshift.processing.image_forge import RECIPE_ID, VERSION, VERSION_2, ParamsV2
from goblinshift.processing.image_forge.geometry import FitMode
from goblinshift.processing.image_forge.params import HexColor
from goblinshift.processing.image_forge.params_v2 import OutputFormatV2
from goblinshift.processing.image_forge.recipe import ImageForgeV2
from goblinshift.processing.output_dir import FileSystemOutputDir
from goblinshift.processing.recipe import ItemOk, ItemRejected, Report
from goblinshift.processing.registry import RECIPES
from tests.image_forge import media
from tests.image_forge.conftest import Workspace, only_ok

JPEG_TOLERANCE = 6


def run_v2(work: Workspace, params: ParamsV2) -> Report:
    return ImageForgeV2().run(params, work.inputs, FileSystemOutputDir(work.out_dir), work.progress)


def close(pixel: object, expected: tuple[int, ...]) -> bool:
    assert isinstance(pixel, tuple)
    return all(abs(a - b) <= JPEG_TOLERANCE for a, b in zip(pixel, expected, strict=True))


def test_v2_cover_gera_quadrado_e_registra_o_corte(work: Workspace) -> None:
    work.add("largo.jpg", media.jpeg(media.solid("RGB", (2400, 1200), (40, 80, 120))))

    item = only_ok(run_v2(work, ParamsV2(fit=FitMode.COVER, max_width=1080, max_height=1080)))

    assert work.open_output("images/largo.jpg").size == (1080, 1080)
    assert (item.details["width_after"], item.details["height_after"]) == (1080, 1080)
    assert item.details["fit"] == "cover"
    assert (item.details["crop_x"], item.details["crop_y"]) == (600, 0)
    assert (item.details["crop_width"], item.details["crop_height"]) == (1200, 1200)
    assert "content_width" not in item.details
    assert "scale_percent" not in item.details


def test_v2_cover_pega_o_centro_da_imagem(work: Workspace) -> None:
    # Esquerda vermelha, centro verde, direita azul: o quadrado do `cover` é todo verde.
    image = Image.new("RGB", (300, 100), (0, 200, 0))
    image.paste((255, 0, 0), (0, 0, 100, 100))
    image.paste((0, 0, 255), (200, 0, 300, 100))
    work.add("faixas.png", media.png(image))

    only_ok(run_v2(work, ParamsV2(fit=FitMode.COVER, max_width=50, max_height=50)))

    output = work.open_output("images/faixas.png")
    assert output.size == (50, 50)
    assert output.getpixel((0, 0)) == (0, 200, 0)
    assert output.getpixel((49, 49)) == (0, 200, 0)


def test_v2_pad_em_jpeg_preenche_a_margem_com_o_fundo(work: Workspace) -> None:
    work.add("a.jpg", media.jpeg(media.solid("RGB", (400, 200), (40, 80, 120))))
    params = ParamsV2(
        fit=FitMode.PAD,
        max_width=400,
        max_height=400,
        output_format=OutputFormatV2.JPEG,
        jpeg_background=HexColor(255, 0, 0),
    )

    item = only_ok(run_v2(work, params))

    output = work.open_output("images/a.jpg")
    assert output.size == (400, 400)
    assert close(output.getpixel((0, 0)), (255, 0, 0))
    assert close(output.getpixel((200, 200)), (40, 80, 120))
    assert (item.details["content_width"], item.details["content_height"]) == (400, 200)
    assert item.details["fit"] == "pad"


def test_v2_pad_em_png_deixa_a_margem_transparente(work: Workspace) -> None:
    work.add("a.png", media.png(media.solid("RGB", (400, 200), (40, 80, 120))))

    only_ok(run_v2(work, ParamsV2(fit=FitMode.PAD, max_width=400, max_height=400)))

    output = work.open_output("images/a.png")
    assert output.size == (400, 400)
    assert output.mode == "RGBA"
    assert output.getchannel("A").getpixel((0, 0)) == 0
    assert output.getpixel((200, 200)) == (40, 80, 120, 255)


def test_v2_pad_sem_margem_nao_ganha_alfa(work: Workspace) -> None:
    work.add("a.png", media.png(media.solid("RGB", (400, 400), (40, 80, 120))))
    only_ok(run_v2(work, ParamsV2(fit=FitMode.PAD, max_width=200, max_height=200)))
    assert work.open_output("images/a.png").mode == "RGB"


def test_v2_escala_em_porcentagem(work: Workspace) -> None:
    work.add("a.png", media.png(media.solid("RGB", (3, 1001), (40, 80, 120))))

    item = only_ok(run_v2(work, ParamsV2(fit=FitMode.SCALE, scale_percent=10)))

    assert work.open_output("images/a.png").size == (1, 100)
    assert (item.details["fit"], item.details["scale_percent"]) == ("scale", 10)


def test_v2_contain_registra_so_o_modo(work: Workspace) -> None:
    work.add("a.png", media.png(media.solid("RGB", (2400, 1200), (40, 80, 120))))
    item = only_ok(run_v2(work, ParamsV2()))
    assert work.open_output("images/a.png").size == (1600, 800)
    assert item.details["fit"] == "contain"
    assert not {"crop_x", "content_width", "scale_percent"} & set(item.details)


@pytest.mark.parametrize(
    ("name", "encode", "before", "after", "output", "pil_format"),
    [
        ("a.webp", media.webp, "webp", "webp", "images/a.webp", "WEBP"),
        ("a.gif", media.gif, "gif", "png", "images/a.png", "PNG"),
    ],
    ids=["webp-fica-webp", "gif-vira-png"],
)
def test_v2_preserve_escolhe_o_formato_de_saida_pela_entrada(
    work: Workspace,
    name: str,
    encode: Callable[[Image.Image], bytes],
    before: str,
    after: str,
    output: str,
    pil_format: str,
) -> None:
    work.add(name, encode(media.solid("RGB", (8, 8), (40, 80, 120))))

    item = only_ok(run_v2(work, ParamsV2()))

    assert (item.details["format_before"], item.details["format_after"]) == (before, after)
    assert work.open_output(output).format == pil_format


def test_v2_converte_para_webp_com_miniatura(work: Workspace) -> None:
    work.add("a.jpg", media.jpeg(media.solid("RGB", (800, 400), (40, 80, 120))))

    only_ok(run_v2(work, ParamsV2(output_format=OutputFormatV2.WEBP, make_thumbnails=True)))

    assert work.files() == ["images/a.webp", "thumbnails/a.webp"]
    assert work.open_output("thumbnails/a.webp").size == (320, 160)


def test_v2_recusa_gif_animado_e_segue_o_lote(work: Workspace) -> None:
    work.add("anim.gif", media.animated_gif(2))
    work.add("ok.gif", media.gif(media.solid("RGB", (8, 8), (1, 2, 3))))

    first, second = run_v2(work, ParamsV2()).items

    assert isinstance(first, ItemRejected) and first.code == "UNSUPPORTED_FORMAT"
    assert isinstance(second, ItemOk)


def test_v1_continua_recusando_webp_e_gif_pela_receita(work: Workspace) -> None:
    work.add("a.webp", media.webp(media.solid("RGB", (8, 8), (1, 2, 3))))
    work.add("b.gif", media.gif(media.solid("RGB", (8, 8), (1, 2, 3))))
    for item in work.run().items:
        assert isinstance(item, ItemRejected) and item.code == "UNSUPPORTED_FORMAT"


def test_v1_e_v2_em_contain_dao_os_mesmos_bytes(
    work: Workspace, tmp_path_factory: pytest.TempPathFactory
) -> None:
    data = media.jpeg(Image.effect_noise((900, 500), 40).convert("RGB"))
    work.add("a.jpg", data)
    work.run()
    other = Workspace(tmp_path_factory.mktemp("v2"))
    other.add("a.jpg", data)
    run_v2(other, ParamsV2())
    assert work.output("images/a.jpg") == other.output("images/a.jpg")


def test_registro_tem_as_duas_versoes() -> None:
    assert {(RECIPE_ID, VERSION), (RECIPE_ID, VERSION_2)} <= set(RECIPES)
