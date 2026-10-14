"""Validação antes de decodificar (1.2, 1.7 e 2.3): formato, quadros, profundidade e pixels."""

import struct
import warnings

import pytest
from PIL import Image

from goblinshift.processing.image_forge.inspection import configure_pillow
from goblinshift.processing.recipe import ItemOk, ItemRejected, Report
from tests.image_forge import media
from tests.image_forge.conftest import Workspace, only_ok


def rejection(report: Report) -> ItemRejected:
    (item,) = report.items
    assert isinstance(item, ItemRejected), item
    return item


def code_for(work: Workspace, data: bytes, name: str = "foto.png") -> str:
    work.add(name, data)
    return rejection(work.run()).code


# --- Limite de pixels, sempre sobre PNG só com cabeçalho: se o código decodificasse, o IDAT lixo
# daria UNSUPPORTED_FORMAT. IMAGE_TOO_LARGE prova que nada foi decodificado; UNSUPPORTED_FORMAT
# na fronteira aceita prova que a validação deixou passar e só a decodificação falhou.


@pytest.mark.parametrize(
    ("size", "expected"),
    [
        ((8192, 1), "UNSUPPORTED_FORMAT"),
        ((8193, 1), "IMAGE_TOO_LARGE"),
        ((1, 8192), "UNSUPPORTED_FORMAT"),
        ((1, 8193), "IMAGE_TOO_LARGE"),
        ((6000, 4000), "UNSUPPORTED_FORMAT"),  # 24 000 000 px, exatamente o teto
        ((6000, 4001), "IMAGE_TOO_LARGE"),
        ((4000, 6001), "IMAGE_TOO_LARGE"),
    ],
)
def test_limite_de_pixels_e_de_eixo_antes_de_decodificar(
    work: Workspace, size: tuple[int, int], expected: str
) -> None:
    assert code_for(work, media.png_header_only(*size)) == expected


def test_png_bomba_e_recusado_por_tamanho(work: Workspace) -> None:
    assert code_for(work, media.png_bomb(30000, 30000)) == "IMAGE_TOO_LARGE"


def test_jpeg_com_sof_gigante_e_recusado_por_tamanho(work: Workspace) -> None:
    small = media.jpeg(media.solid("RGB", (8, 8), (10, 20, 30)))
    assert code_for(work, media.with_sof_size(small, 65000, 65000), "x.jpg") == "IMAGE_TOO_LARGE"


def test_bloco_de_texto_bomba_rejeita_so_o_arquivo(work: Workspace) -> None:
    good = media.png(media.solid("RGB", (4, 4), (1, 2, 3)))
    work.add("bomba.png", media.with_text_bomb(good, 50 * 1024 * 1024))
    work.add("boa.png", good)

    report = work.run()

    first, second = report.items
    assert isinstance(first, ItemRejected)
    assert first.code == "UNSUPPORTED_FORMAT"
    assert isinstance(second, ItemOk)


def test_segunda_barreira_do_pillow_vira_image_too_large(
    work: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Com o teto global abaixo da imagem, o próprio Image.open avisa (DecompressionBombWarning);
    # a receita trata o aviso como recusa por tamanho, não como formato.
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 10)
    assert code_for(work, media.png(media.solid("RGB", (4, 4), (0, 0, 0)))) == "IMAGE_TOO_LARGE"


def test_configure_pillow_fixa_o_teto_e_transforma_o_aviso_em_erro(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", Image.MAX_IMAGE_PIXELS)
    with warnings.catch_warnings():
        configure_pillow()
        assert Image.MAX_IMAGE_PIXELS == 24_000_000
        with pytest.raises(Image.DecompressionBombWarning):
            warnings.warn("teste", Image.DecompressionBombWarning, stacklevel=1)


# --- Profundidade de 16 bits: o cinza aparece no modo, o colorido só no IHDR.


def test_png_cinza_de_16_bits_gravado_pelo_pillow(work: Workspace) -> None:
    gray16 = media.png(media.solid("I;16", (4, 4), 1000))
    assert code_for(work, gray16) == "UNSUPPORTED_BIT_DEPTH"


@pytest.mark.parametrize("color_type", [0, 2, 4, 6])
def test_png_de_16_bits_em_qualquer_tipo_de_cor(work: Workspace, color_type: int) -> None:
    assert code_for(work, media.png_16_bit(4, 3, color_type)) == "UNSUPPORTED_BIT_DEPTH"


# --- Formatos fora da lista.


@pytest.mark.parametrize(
    ("name", "data"),
    [
        ("a.gif", media.encoded(media.solid("RGB", (4, 4), (1, 2, 3)), "GIF")),
        ("a.webp", media.encoded(media.solid("RGB", (4, 4), (1, 2, 3)), "WEBP")),
        ("a.bmp", media.encoded(media.solid("RGB", (4, 4), (1, 2, 3)), "BMP")),
        ("a.tiff", media.encoded(media.solid("RGB", (4, 4), (1, 2, 3)), "TIFF")),
        ("vazio.jpg", b""),
        ("texto.jpg", b"nao sou uma imagem\n"),
        ("cmyk.jpg", media.jpeg(media.solid("CMYK", (4, 4), (0, 0, 0, 255)))),
    ],
)
def test_formato_ou_modo_fora_da_lista(work: Workspace, name: str, data: bytes) -> None:
    assert code_for(work, data, name) == "UNSUPPORTED_FORMAT"


def test_jpeg_truncado(work: Workspace) -> None:
    noise = Image.effect_noise((64, 64), 50).convert("RGB")
    truncated = media.jpeg(noise)[:-200]
    assert code_for(work, truncated, "cortado.jpg") == "UNSUPPORTED_FORMAT"


def test_png_animado_e_recusado(work: Workspace) -> None:
    frames = [media.solid("RGB", (4, 4), (255, 0, 0)), media.solid("RGB", (4, 4), (0, 0, 255))]
    assert code_for(work, media.apng(frames)) == "UNSUPPORTED_FORMAT"


def test_motivo_nao_expoe_o_caminho_interno(work: Workspace) -> None:
    work.add("texto.jpg", b"nao sou uma imagem\n")
    reason = rejection(work.run()).reason
    assert str(work.inputs_dir) not in reason
    assert "0000" not in reason


# --- MPO: o quadro principal, sem ser tratado como animação.


def test_mpo_usa_so_o_quadro_principal(work: Workspace) -> None:
    main = media.solid("RGB", (40, 30), (200, 30, 30))
    depth = media.solid("RGB", (20, 10), (30, 30, 200))
    work.add("iphone.jpg", media.mpo([main, depth]))

    item = only_ok(work.run())

    assert item.details["mpo_frames_discarded"] == 1
    assert item.details["format_before"] == "mpo"
    output = work.open_output("images/iphone.jpg")
    assert output.format == "JPEG"
    assert output.size == (40, 30)
    red, green, blue = output.getpixel((20, 15))  # type: ignore[misc]
    assert red > 150 and green < 80 and blue < 80


def test_jpeg_comum_nao_descarta_quadros(work: Workspace) -> None:
    work.add("a.jpg", media.jpeg(media.solid("RGB", (8, 8), (1, 2, 3))))
    assert only_ok(work.run()).details["mpo_frames_discarded"] == 0


def test_png_animado_de_um_quadro_e_estatico(work: Workspace) -> None:
    # Ambiguidade A7: só n_frames > 1 é animação.
    still = media.png(media.solid("RGB", (4, 4), (1, 2, 3)))
    actl = media.chunk(b"acTL", struct.pack(">II", 1, 0))
    fctl = media.chunk(b"fcTL", struct.pack(">IIIIIHHBB", 0, 4, 4, 0, 0, 1, 1, 0, 0))
    work.add("um-quadro.png", media.insert_after_ihdr(still, actl + fctl))

    assert only_ok(work.run()).details["format_before"] == "png"
