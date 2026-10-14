"""Orientação EXIF aplicada antes de remover o EXIF (1.2), pelas 8 orientações da EXIF 2.32.

A expectativa sai da definição da tag 0x0112 ("onde ficam visualmente a linha 0 e a coluna 0 da
imagem gravada"), não da tabela de transposições do código: o pixel gravado em (0, 0) vai para o
canto onde essas duas bordas se encontram, e o fim da linha 0 vai para a outra ponta dela.
"""

import pytest

from tests.image_forge import media
from tests.image_forge.conftest import Workspace, only_ok
from tests.image_forge.test_inspection import code_for

RED = (255, 0, 0)
GREEN = (0, 255, 0)
GRAY = (128, 128, 128)
WIDTH, HEIGHT = 4, 2

# orientação: (canto do pixel gravado em (0, 0), canto do fim da linha 0)
CORNERS = {
    1: ("topo-esq", "topo-dir"),
    2: ("topo-dir", "topo-esq"),
    3: ("base-dir", "base-esq"),
    4: ("base-esq", "base-dir"),
    5: ("topo-esq", "base-esq"),
    6: ("topo-dir", "base-dir"),
    7: ("base-dir", "topo-dir"),
    8: ("base-esq", "topo-esq"),
}


def corner(name: str, size: tuple[int, int]) -> tuple[int, int]:
    width, height = size
    vertical, horizontal = name.split("-")
    return (0 if horizontal == "esq" else width - 1, 0 if vertical == "topo" else height - 1)


@pytest.mark.parametrize("orientation", sorted(CORNERS))
def test_as_oito_orientacoes(work: Workspace, orientation: int) -> None:
    stored = media.solid("RGB", (WIDTH, HEIGHT), GRAY)
    stored.putpixel((0, 0), RED)
    stored.putpixel((WIDTH - 1, 0), GREEN)
    work.add("a.png", media.png(stored, exif=media.exif(orientation=orientation)))

    item = only_ok(work.run())

    output = work.open_output("images/a.png")
    swapped = orientation >= 5
    expected_size = (HEIGHT, WIDTH) if swapped else (WIDTH, HEIGHT)
    assert output.size == expected_size
    assert (item.details["width_before"], item.details["height_before"]) == expected_size
    assert item.details["orientation_applied"] == orientation
    origin, row_end = CORNERS[orientation]
    assert output.getpixel(corner(origin, expected_size)) == RED
    assert output.getpixel(corner(row_end, expected_size)) == GREEN


def test_sem_exif_a_orientacao_e_a_normal(work: Workspace) -> None:
    work.add("a.png", media.png(media.solid("RGB", (4, 2), GRAY)))
    assert only_ok(work.run()).details["orientation_applied"] == 1


def test_foto_de_celular_em_jpeg_com_orientacao_6(work: Workspace) -> None:
    stored = media.solid("RGB", (32, 16), (0, 0, 255))
    stored.paste(RED, (0, 0, 16, 16))  # metade esquerda gravada = coluna 0
    work.add("iphone.jpg", media.jpeg(stored, exif=media.exif(orientation=6)))

    only_ok(work.run())

    output = work.open_output("images/iphone.jpg")
    assert output.size == (16, 32)
    top = output.getpixel((8, 4))
    bottom = output.getpixel((8, 28))
    assert isinstance(top, tuple) and isinstance(bottom, tuple)
    assert top[0] > 200 and top[2] < 60  # a coluna 0 fica no topo
    assert bottom[2] > 200 and bottom[0] < 60


@pytest.mark.parametrize("orientation", [0, 9, 65535])
def test_orientacao_fora_da_tag_e_recusada(work: Workspace, orientation: int) -> None:
    data = media.png(media.solid("RGB", (4, 2), GRAY), exif=media.exif(orientation=orientation))
    assert code_for(work, data) == "UNSUPPORTED_FORMAT"


def test_exif_ilegivel_e_recusado_sem_adivinhar(work: Workspace) -> None:
    corrupt = b"Exif\x00\x00MM\x00\x2a\x00\x00\x00\x08\xff\xff\x01"
    data = media.jpeg(media.solid("RGB", (4, 2), GRAY), exif=corrupt)
    assert code_for(work, data, "a.jpg") == "UNSUPPORTED_FORMAT"
