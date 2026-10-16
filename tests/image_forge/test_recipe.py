"""Receita image-forge v1 sobre o contrato de 3.2 (docs/GoblinShiftPlan.md, 1.1, 1.2 e 1.7)."""

import pytest
from PIL import Image

from goblinshift.processing.image_forge import OutputFormat, Params
from goblinshift.processing.image_forge.recipe import ImageForgeV1
from goblinshift.processing.output_dir import FileSystemOutputDir
from goblinshift.processing.recipe import (
    InputContractError,
    ItemOk,
    ItemRejected,
    Preview,
    RecipeFailure,
)
from tests.image_forge import media
from tests.image_forge.conftest import Workspace, only_ok

DETAIL_KEYS = {
    "width_before",
    "height_before",
    "bytes_before",
    "format_before",
    "width_after",
    "height_after",
    "format_after",
    "color_profile",
    "orientation_applied",
    "mpo_frames_discarded",
}


def rgb_png(size: tuple[int, int]) -> bytes:
    return media.png(media.solid("RGB", size, (40, 80, 120)))


def rgb_jpeg(size: tuple[int, int]) -> bytes:
    return media.jpeg(media.solid("RGB", size, (40, 80, 120)))


def noise_jpeg() -> bytes:
    return media.jpeg(Image.effect_noise((64, 64), 60).convert("RGB"))


# --- Ajuste e exemplos de 1.2.


def test_exemplo_2400x1200_vira_1600x800(work: Workspace) -> None:
    work.add("grande.jpg", rgb_jpeg((2400, 1200)))

    item = only_ok(work.run())

    assert item.outputs == ("images/grande.jpg",)
    assert work.open_output("images/grande.jpg").size == (1600, 800)
    assert (item.details["width_before"], item.details["height_before"]) == (2400, 1200)
    assert (item.details["width_after"], item.details["height_after"]) == (1600, 800)


def test_exemplo_200x100_nao_amplia(work: Workspace) -> None:
    work.add("pequena.png", rgb_png((200, 100)))
    only_ok(work.run())
    assert work.open_output("images/pequena.png").size == (200, 100)


def test_caixa_assimetrica(work: Workspace) -> None:
    work.add("a.png", rgb_png((300, 300)))
    only_ok(work.run(Params(max_width=100, max_height=50)))
    assert work.open_output("images/a.png").size == (50, 50)


def test_relatorio_registra_o_filtro_e_a_caixa_da_miniatura(work: Workspace) -> None:
    work.add("a.png", rgb_png((4, 4)))
    assert work.run().details == {"resampling": "lanczos", "thumbnail_box": 320}


def test_detalhes_do_item_sem_avisos_nem_contagens(work: Workspace) -> None:
    data = rgb_png((4, 4))
    work.add("a.png", data)

    item = only_ok(work.run())

    assert set(item.details) == DETAIL_KEYS
    assert item.details["bytes_before"] == len(data)
    assert (item.details["format_before"], item.details["format_after"]) == ("png", "png")
    assert item.warnings == ()
    assert item.counts == {}


# --- Formato de saída.


@pytest.mark.parametrize(
    ("name", "data", "output_format", "expected"),
    [
        ("a.jpg", rgb_jpeg((8, 8)), OutputFormat.PRESERVE, "images/a.jpg"),
        ("a.png", rgb_png((8, 8)), OutputFormat.PRESERVE, "images/a.png"),
        ("a.png", rgb_png((8, 8)), OutputFormat.JPEG, "images/a.jpg"),
        ("a.jpg", rgb_jpeg((8, 8)), OutputFormat.PNG, "images/a.png"),
        ("a.jpeg", rgb_jpeg((8, 8)), OutputFormat.PRESERVE, "images/a.jpg"),
        ("a.JPG", rgb_jpeg((8, 8)), OutputFormat.PRESERVE, "images/a.jpg"),
        ("sem-extensao", rgb_png((8, 8)), OutputFormat.PRESERVE, "images/sem-extensao.png"),
        # Extensão mentirosa: o formato vem do conteúdo, não do nome.
        ("png-de-verdade.jpg", rgb_png((8, 8)), OutputFormat.PRESERVE, "images/png-de-verdade.png"),
    ],
)
def test_formato_e_nome_da_saida(
    work: Workspace, name: str, data: bytes, output_format: OutputFormat, expected: str
) -> None:
    work.add(name, data)

    item = only_ok(work.run(Params(output_format=output_format)))

    assert item.outputs == (expected,)
    assert work.open_output(expected).format == ("PNG" if expected.endswith(".png") else "JPEG")


def test_qualidade_jpeg_chega_ao_encoder(work: Workspace) -> None:
    work.add("a.jpg", noise_jpeg())
    work.add("b.jpg", noise_jpeg())
    recipe = ImageForgeV1()
    low = Params(jpeg_quality=60)
    high = Params(jpeg_quality=95)
    out_low = FileSystemOutputDir(work.out_dir / "baixa")
    out_high = FileSystemOutputDir(work.out_dir / "alta")
    (work.out_dir / "baixa").mkdir()
    (work.out_dir / "alta").mkdir()

    recipe.run(low, work.inputs[:1], out_low, work.progress)
    recipe.run(high, work.inputs[:1], out_high, work.progress)

    small = len(work.output("baixa/images/a.jpg"))
    large = len(work.output("alta/images/a.jpg"))
    assert small < large


def test_png_nao_recebe_a_qualidade_do_jpeg(work: Workspace) -> None:
    work.add("a.png", media.png(Image.effect_noise((32, 32), 60).convert("RGB")))
    (work.out_dir / "q60").mkdir()
    (work.out_dir / "q95").mkdir()

    for quality in (60, 95):
        ImageForgeV1().run(
            Params(jpeg_quality=quality),
            work.inputs,
            FileSystemOutputDir(work.out_dir / f"q{quality}"),
            work.progress,
        )

    assert work.output("q60/images/a.png") == work.output("q95/images/a.png")


def test_mesma_entrada_produz_os_mesmos_bytes(work: Workspace) -> None:
    work.add("a.jpg", noise_jpeg())
    work.add("b.png", media.png(Image.effect_noise((32, 32), 60).convert("L")))
    for run in ("um", "dois"):
        (work.out_dir / run).mkdir()
        ImageForgeV1().run(
            Params(make_thumbnails=True),
            work.inputs,
            FileSystemOutputDir(work.out_dir / run),
            work.progress,
        )

    for name in ("images/a.jpg", "thumbnails/a.jpg", "images/b.png", "thumbnails/b.png"):
        assert work.output(f"um/{name}") == work.output(f"dois/{name}")


# --- Miniaturas.


def test_sem_miniaturas_nada_em_thumbnails(work: Workspace) -> None:
    work.add("a.png", rgb_png((400, 200)))

    item = only_ok(work.run())

    assert item.outputs == ("images/a.png",)
    assert not (work.out_dir / "thumbnails").exists()


def test_miniatura_cabe_em_320_sobre_a_imagem_ja_redimensionada(work: Workspace) -> None:
    work.add("a.jpg", rgb_jpeg((2400, 1200)))

    item = only_ok(work.run(Params(make_thumbnails=True)))

    assert item.outputs == ("images/a.jpg", "thumbnails/a.jpg")
    assert work.open_output("thumbnails/a.jpg").size == (320, 160)


def test_miniatura_nao_amplia(work: Workspace) -> None:
    work.add("a.png", rgb_png((100, 50)))

    only_ok(work.run(Params(make_thumbnails=True)))

    assert work.open_output("thumbnails/a.png").size == (100, 50)


def test_miniatura_tem_o_formato_da_saida(work: Workspace) -> None:
    work.add("a.png", rgb_png((400, 400)))

    item = only_ok(work.run(Params(make_thumbnails=True, output_format=OutputFormat.JPEG)))

    assert item.outputs == ("images/a.jpg", "thumbnails/a.jpg")


# --- Lote.


def test_imagem_invalida_no_meio_nao_bloqueia_as_demais(work: Workspace) -> None:
    work.add("a.png", rgb_png((8, 8)))
    work.add("b.jpg", b"nao sou imagem")
    work.add("c.png", rgb_png((8, 8)))

    report = work.run()

    first, second, third = report.items
    assert isinstance(first, ItemOk) and first.position == 0
    assert isinstance(second, ItemRejected) and second.position == 1
    assert isinstance(third, ItemOk) and third.position == 2
    assert work.progress.done == [0, 1, 2]


def test_todas_invalidas_relatorio_so_com_rejeicoes(work: Workspace) -> None:
    work.add("a.jpg", b"")
    work.add("b.png", media.png_16_bit(2, 2, 2))

    report = work.run()

    assert [type(item) for item in report.items] == [ItemRejected, ItemRejected]
    assert not report.has_outputs
    assert work.files() == []


def test_outputs_citam_exatamente_os_arquivos_criados(work: Workspace) -> None:
    work.add("Foto.jpg", rgb_jpeg((8, 8)))
    work.add("foto.JPG", rgb_jpeg((8, 8)))  # colide por casefold
    work.add("foto.png", rgb_png((8, 8)))  # extensão diferente: não colide

    report = work.run(Params(make_thumbnails=True))

    cited = sorted(
        name for item in report.items if isinstance(item, ItemOk) for name in item.outputs
    )
    assert cited == work.files()
    assert cited == sorted(
        [
            "images/Foto.jpg",
            "images/foto_1.jpg",
            "images/foto.png",
            "thumbnails/Foto.jpg",
            "thumbnails/foto_1.jpg",
            "thumbnails/foto.png",
        ]
    )


@pytest.mark.parametrize(
    ("original", "expected"),
    [
        ("../../etc/passwd.png", "images/_.._etc_passwd.png"),
        (".png", "images/png.png"),  # nome oculto sem extensão: o ponto inicial sai
        ("..png", "images/imagem.png"),
        ("ferias\x00.png", "images/ferias_.png"),
    ],
)
def test_nome_original_e_dado_nunca_caminho(work: Workspace, original: str, expected: str) -> None:
    work.add(original, rgb_png((4, 4)))
    assert only_ok(work.run()).outputs == (expected,)


def test_acima_do_limite_de_entradas_e_fora_do_contrato(work: Workspace) -> None:
    for index in range(3):
        work.add(f"{index}.png", rgb_png((4, 4)))

    with pytest.raises(InputContractError, match="3 imagens"):
        work.run(recipe=ImageForgeV1(max_inputs=2))


def test_estouro_do_teto_de_saida_derruba_a_execucao(work: Workspace) -> None:
    work.add("a.png", media.png(Image.effect_noise((64, 64), 60).convert("RGB")))
    tiny = FileSystemOutputDir(work.out_dir, max_bytes=100)

    with pytest.raises(RecipeFailure) as caught:
        ImageForgeV1().run(Params(), work.inputs, tiny, work.progress)

    assert caught.value.code == "OUTPUT_LIMIT_EXCEEDED"


def test_falha_de_leitura_da_entrada_e_infraestrutura(work: Workspace) -> None:
    work.add("a.png", rgb_png((4, 4)))
    work.inputs[0].path.unlink()

    with pytest.raises(FileNotFoundError):
        work.run()


# --- Preview: imagem reduzida, sempre JPEG, depois de todo o conteúdo (2.2, 3.6).


def test_preview_jpeg_cabe_em_512_e_deriva_da_saida(work: Workspace) -> None:
    work.add("a.png", rgb_png((2400, 1200)))

    item = only_ok(work.run())

    assert item.previews == (Preview("previews/a.jpg", "images/a.png"),)
    preview = work.open_output("previews/a.jpg")
    assert (preview.format, preview.size) == ("JPEG", (512, 256))


def test_preview_de_imagem_pequena_nao_amplia(work: Workspace) -> None:
    work.add("a.png", rgb_png((100, 50)))

    only_ok(work.run())

    assert work.open_output("previews/a.jpg").size == (100, 50)


def test_preview_de_imagem_com_transparencia_sai_sem_alfa(work: Workspace) -> None:
    work.add("a.png", media.png(media.solid("RGBA", (64, 64), (10, 20, 30, 0))))

    only_ok(work.run())

    assert work.open_output("previews/a.jpg").mode == "RGB"


def test_sem_espaco_o_preview_some_e_o_conteudo_fica(work: Workspace) -> None:
    work.add("a.png", rgb_png((64, 64)))
    one_file = FileSystemOutputDir(work.out_dir, max_files=1)

    report = ImageForgeV1().run(Params(), work.inputs, one_file, work.progress)

    [item] = report.items
    assert isinstance(item, ItemOk)
    assert (item.outputs, item.previews) == (("images/a.png",), ())
    assert work.previews() == []


def test_previews_vem_depois_de_todas_as_saidas(work: Workspace) -> None:
    work.add("a.png", rgb_png((64, 64)))
    work.add("b.png", rgb_png((64, 64)))
    out = FileSystemOutputDir(work.out_dir)

    ImageForgeV1().run(Params(make_thumbnails=True), work.inputs, out, work.progress)

    assert [name for name, _role in out.created] == [
        "images/a.png",
        "thumbnails/a.png",
        "images/b.png",
        "thumbnails/b.png",
        "previews/a.jpg",
        "previews/b.jpg",
    ]
