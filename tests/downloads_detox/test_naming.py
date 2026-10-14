"""Nome lógico e grupo de cada saída do Detox (1.4, 3.6; ambiguidade A17).

Colisões, nomes reservados e o teto de 180 bytes ficam com o `OutputDir` e são cobertos em
`test_recipe.py`, com o diretório de verdade.
"""

import unicodedata

import pytest

from goblinshift.processing.downloads_detox import GroupBy, NameMode, Params
from goblinshift.processing.downloads_detox.classify import Format
from goblinshift.processing.downloads_detox.naming import Destination, destination
from goblinshift.processing.recipe import OutputGroup

INDEXED = Params(name_mode=NameMode.INDEXED)


@pytest.mark.parametrize(
    ("fmt", "group"),
    [
        (Format.JPEG, OutputGroup.IMAGES),
        (Format.PNG, OutputGroup.IMAGES),
        (Format.CSV, OutputGroup.TABLES),
        (Format.TXT, OutputGroup.TEXT),
        (Format.ZIP, OutputGroup.ARCHIVES),
    ],
)
def test_group_by_kind_usa_o_grupo_do_tipo(fmt: Format, group: OutputGroup) -> None:
    assert destination("x", 0, fmt, Params()).group is group


def test_group_by_none_vai_para_a_raiz() -> None:
    assert destination("Foto.png", 0, Format.PNG, Params(group_by=GroupBy.NONE)) == Destination(
        "Foto.png", None
    )


@pytest.mark.parametrize(
    ("original", "expected"),
    [
        pytest.param("Foto.png", "Foto.png", id="simples"),
        pytest.param("foto.PNG", "foto.PNG", id="caixa-da-extensao-preservada"),
        pytest.param("relatório final.txt", "relatório final.txt", id="espaco-e-acento"),
        pytest.param("a/b.txt", "a_b.txt", id="barra"),
        pytest.param("a\\b.txt", "a_b.txt", id="contrabarra"),
        pytest.param("../../etc/passwd.txt", "_.._etc_passwd.txt", id="travessia"),
        pytest.param("a\x01\nb.csv", "a__b.csv", id="controles"),
        pytest.param("...txt", "file.txt", id="so-pontos-no-radical"),
        pytest.param(".png", "png", id="so-extensao"),
        pytest.param("arquivo.tar.zip", "arquivo.tar.zip", id="ponto-no-radical"),
    ],
)
def test_preserve_usa_o_nome_seguro(original: str, expected: str) -> None:
    assert destination(original, 0, Format.TXT, Params()).logical_name == expected


def test_preserve_normaliza_para_nfc() -> None:
    nfd = unicodedata.normalize("NFD", "Relatório.txt")

    assert destination(nfd, 0, Format.TXT, Params()).logical_name == "Relatório.txt"


@pytest.mark.parametrize(
    ("position", "fmt", "expected"),
    [
        pytest.param(0, Format.PNG, "file-001.png", id="primeira"),
        pytest.param(3, Format.JPEG, "file-004.jpg", id="jpeg-canonico"),
        pytest.param(1, Format.CSV, "file-002.csv", id="csv"),
        pytest.param(2, Format.TXT, "file-003.txt", id="txt"),
        pytest.param(99, Format.ZIP, "file-100.zip", id="centesima"),
        pytest.param(999, Format.ZIP, "file-1000.zip", id="quatro-digitos"),
    ],
)
def test_indexed_usa_a_posicao_original_a_partir_de_1(
    position: int, fmt: Format, expected: str
) -> None:
    assert destination("qualquer.JPEG", position, fmt, INDEXED).logical_name == expected


def test_indexed_usa_o_prefixo_escolhido() -> None:
    params = Params(name_mode=NameMode.INDEXED, name_prefix="viagem")

    assert destination("x.png", 0, Format.PNG, params).logical_name == "viagem-001.png"


def test_preserve_ignora_o_prefixo() -> None:
    params = Params(name_prefix="viagem")

    assert destination("x.png", 0, Format.PNG, params).logical_name == "x.png"
