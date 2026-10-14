"""Texto UTF-8 incremental e marcação reconhecível em `.txt` e `.csv` (1.4; ambiguidade A20)."""

import io

import pytest

from goblinshift.processing.downloads_detox.classify import (
    TEXT_BLOCK_BYTES,
    Format,
    Refusal,
    classify,
)

BOM = b"\xef\xbb\xbf"
TWO_BYTE = "é".encode()
FOUR_BYTE = "😀".encode()


def classified(name: str, data: bytes) -> Format | Refusal:
    return classify(io.BytesIO(data), name)


@pytest.mark.parametrize(
    "data",
    [
        pytest.param(b"nome,cidade\r\nAna,Recife\r\n", id="ascii"),
        pytest.param("ação, 😀, 漢字\n".encode(), id="multibyte"),
        pytest.param(BOM + b"a,b\n", id="com-bom"),
        pytest.param(b"\t \r\n", id="so-espacos"),
        pytest.param(b"a\x01b\x1bc", id="controle-que-nao-e-nul"),
        pytest.param(b"texto sobre <html> no meio", id="html-no-meio"),
        pytest.param(b"< html>", id="tag-com-espaco"),
        pytest.param(b"<htm", id="prefixo-incompleto"),
    ],
)
def test_texto_utf8_e_aceito(data: bytes) -> None:
    assert classified("dados.txt", data) is Format.TXT


def test_multibyte_cortado_na_fronteira_do_bloco_e_valido() -> None:
    # O caractere de 4 bytes começa 2 bytes antes do fim do primeiro bloco.
    data = b"a" * (TEXT_BLOCK_BYTES - 2) + FOUR_BYTE + b"fim"

    assert classified("grande.txt", data) is Format.TXT


def test_multibyte_truncado_no_fim_do_arquivo_e_recusado() -> None:
    data = b"abc" + TWO_BYTE[:1]

    assert classified("cortado.txt", data) == Refusal(
        "o texto termina no meio de um caractere UTF-8"
    )


def test_multibyte_truncado_no_fim_de_arquivo_maior_que_um_bloco_e_recusado() -> None:
    data = b"a" * TEXT_BLOCK_BYTES + FOUR_BYTE[:3]

    assert isinstance(classified("cortado.csv", data), Refusal)


@pytest.mark.parametrize(
    "data",
    [
        pytest.param("café com leite".encode("latin-1"), id="latin1"),
        pytest.param(b"\xc0\xaf", id="overlong"),
        pytest.param(b"\xed\xa0\x80", id="surrogate"),
        pytest.param(b"a" * TEXT_BLOCK_BYTES + b"\xff", id="invalido-no-segundo-bloco"),
    ],
)
def test_sequencia_invalida_e_recusada(data: bytes) -> None:
    assert classified("dados.txt", data) == Refusal("o conteúdo não é texto UTF-8 válido")


@pytest.mark.parametrize(
    "data",
    [
        pytest.param(b"a\x00b", id="no-inicio"),
        pytest.param(b"a" * TEXT_BLOCK_BYTES + b"\x00", id="no-segundo-bloco"),
    ],
)
def test_nul_e_recusado(data: bytes) -> None:
    assert classified("dados.csv", data) == Refusal("o texto contém o caractere NUL")


@pytest.mark.parametrize(
    "opening",
    [
        "<!DOCTYPE html>",
        "<!doctype HTML>",
        "<html>",
        "<HEAD>",
        "<body>",
        "<script>alert(1)</script>",
        "<svg xmlns='http://www.w3.org/2000/svg'/>",
        "<?xml version='1.0'?>",
    ],
)
@pytest.mark.parametrize(
    "lead",
    [
        pytest.param(b"", id="direto"),
        pytest.param(BOM, id="bom"),
        pytest.param(b" \t\r\n", id="espacos"),
        pytest.param(BOM + b"\n\n  ", id="bom-e-espacos"),
    ],
)
@pytest.mark.parametrize("extension", [".txt", ".csv"])
def test_marcacao_reconhecivel_e_recusada(opening: str, lead: bytes, extension: str) -> None:
    result = classified(f"pagina{extension}", lead + opening.encode())

    assert isinstance(result, Refusal)
    assert "HTML, SVG ou XML" in result.reason


def test_marcacao_depois_de_um_bloco_inteiro_de_espacos_e_recusada() -> None:
    data = b" " * (TEXT_BLOCK_BYTES + 10) + b"<html>"

    assert isinstance(classified("pagina.txt", data), Refusal)
