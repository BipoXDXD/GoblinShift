"""Classificação por assinatura e extensão (docs/GoblinShiftPlan.md, 1.4; ambiguidade A11)."""

import io

import pytest

from goblinshift.processing.downloads_detox.classify import Format, Kind, Refusal, classify
from tests.downloads_detox import samples

ACCEPTED_EXTENSIONS = (".jpg", ".jpeg", ".png", ".csv", ".txt", ".zip")


def classified(name: str, data: bytes) -> Format | Refusal:
    return classify(io.BytesIO(data), name)


# Conteúdo de cada formato e as extensões coerentes com ele.
BY_CONTENT = {
    "jpeg": (samples.jpeg(), {".jpg": Format.JPEG, ".jpeg": Format.JPEG}),
    "png": (samples.png(), {".png": Format.PNG}),
    "zip": (samples.zip_archive({"a.txt": b"a"}), {".zip": Format.ZIP}),
    "texto": (samples.text("a,b\n1,2\n"), {".csv": Format.CSV, ".txt": Format.TXT}),
}
MATRIX = [
    pytest.param(data, extension, coherent.get(extension), id=f"{content}{extension}")
    for content, (data, coherent) in BY_CONTENT.items()
    for extension in ACCEPTED_EXTENSIONS
]


@pytest.mark.parametrize(("data", "extension", "expected"), MATRIX)
def test_cada_assinatura_com_cada_extensao_aceita(
    data: bytes, extension: str, expected: Format | None
) -> None:
    result = classified(f"arquivo{extension}", data)

    if expected is None:
        assert isinstance(result, Refusal)
    else:
        assert result is expected


@pytest.mark.parametrize(
    ("fmt", "kind", "extension"),
    [
        (Format.JPEG, Kind.IMAGE, ".jpg"),
        (Format.PNG, Kind.IMAGE, ".png"),
        (Format.CSV, Kind.TABLE, ".csv"),
        (Format.TXT, Kind.TEXT, ".txt"),
        (Format.ZIP, Kind.ARCHIVE, ".zip"),
    ],
)
def test_formato_sabe_o_tipo_e_a_extensao_canonica(fmt: Format, kind: Kind, extension: str) -> None:
    assert (fmt.kind, fmt.extension) == (kind, extension)


@pytest.mark.parametrize("name", ["FOTO.JPG", "foto.Jpeg", "foto.PnG"])
def test_extensao_e_comparada_sem_caixa(name: str) -> None:
    data = samples.png() if name.casefold().endswith(".png") else samples.jpeg()

    assert isinstance(classified(name, data), Format)


def test_jpeg_de_tres_bytes_e_imagem() -> None:
    assert classified("mini.jpg", samples.JPEG_SIGNATURE) is Format.JPEG


def test_jpeg_chamado_png_e_recusado_com_motivo() -> None:
    result = classified("foto.png", samples.jpeg())

    assert result == Refusal("o conteúdo é JPEG, mas a extensão é .png")


def test_zip_chamado_txt_e_recusado_com_motivo() -> None:
    result = classified("notas.txt", samples.zip_archive({"a": b"a"}))

    assert result == Refusal("o conteúdo é ZIP, mas a extensão é .txt")


def test_texto_chamado_png_e_recusado_com_motivo() -> None:
    result = classified("foto.png", samples.text())

    assert result == Refusal("o conteúdo não tem a assinatura de PNG")


@pytest.mark.parametrize("name", ["vazio.jpg", "vazio.png", "vazio.zip"])
def test_binario_vazio_e_recusado(name: str) -> None:
    assert isinstance(classified(name, b""), Refusal)


@pytest.mark.parametrize("name", ["vazio.txt", "vazio.csv"])
def test_texto_vazio_e_aceito(name: str) -> None:
    assert isinstance(classified(name, b""), Format)


def test_zip_que_e_jar_passa_como_zip_opaco() -> None:
    assert classified("ferramenta.zip", samples.jar()) is Format.ZIP


def test_zip_vazio_e_aceito() -> None:
    assert classified("vazio.zip", samples.EMPTY_ZIP) is Format.ZIP


@pytest.mark.parametrize(
    ("name", "data"),
    [
        pytest.param("ferramenta.jar", samples.jar(), id="jar"),
        pytest.param("carta.docx", samples.docx(), id="docx"),
        pytest.param("planilha.xlsx", samples.zip_archive({"xl/a.xml": b"<a/>"}), id="xlsx"),
        pytest.param("app.apk", samples.zip_archive({"classes.dex": b"dex"}), id="apk"),
        pytest.param("setup.exe", samples.PE_EXECUTABLE, id="exe"),
        pytest.param("pagina.html", samples.text("<html></html>"), id="html"),
        pytest.param("desenho.svg", samples.text("<svg/>"), id="svg"),
        pytest.param("app.js", samples.text("alert(1)"), id="js"),
        pytest.param("run.sh", samples.SHELL_SCRIPT, id="sh"),
        pytest.param("dados.json", samples.text("{}"), id="json"),
        pytest.param("README", samples.text(), id="sem-extensao"),
        pytest.param(".txt", samples.text(), id="so-extensao-oculta"),
        pytest.param("nota.txt.exe", samples.text(), id="extensao-dupla"),
        pytest.param("lista.c\u017fv", samples.text(), id="casefold-nao-ascii"),
    ],
)
def test_extensao_fora_da_lista_e_recusada(name: str, data: bytes) -> None:
    result = classified(name, data)

    assert isinstance(result, Refusal)
    assert "extensão" in result.reason


@pytest.mark.parametrize(
    "data",
    [
        pytest.param(samples.PE_EXECUTABLE, id="pe"),
        pytest.param(samples.ELF_EXECUTABLE, id="elf"),
        pytest.param(samples.MACHO_32, id="macho-32"),
        pytest.param(samples.MACHO_64, id="macho-64"),
        pytest.param(samples.MACHO_32_REVERSED, id="macho-32-invertido"),
        pytest.param(samples.MACHO_64_REVERSED, id="macho-64-invertido"),
        pytest.param(samples.MACHO_FAT, id="macho-fat"),
        pytest.param(samples.SHELL_SCRIPT, id="script"),
    ],
)
@pytest.mark.parametrize("extension", ACCEPTED_EXTENSIONS)
def test_executavel_e_script_sao_recusados_com_qualquer_extensao(
    data: bytes, extension: str
) -> None:
    result = classified(f"inocente{extension}", data)

    assert isinstance(result, Refusal)
    assert "executável ou script" in result.reason
