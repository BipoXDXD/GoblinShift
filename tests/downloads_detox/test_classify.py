"""Classificação por assinatura e extensão (docs/GoblinShiftPlan.md, 1.4; ambiguidade A11)."""

import io

import pytest

from goblinshift.processing.downloads_detox.classify import (
    TEXT_BLOCK_BYTES,
    V1_FORMATS,
    V2_FORMATS,
    Format,
    Kind,
    Refusal,
    classify,
)
from tests.downloads_detox import samples

ACCEPTED_EXTENSIONS = (".jpg", ".jpeg", ".png", ".csv", ".txt", ".zip")


def classified(name: str, data: bytes) -> Format | Refusal:
    return classify(io.BytesIO(data), name, V1_FORMATS)


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


# --- Versão 2: PDF, WebP e GIF pela assinatura, só no primeiro bloco ---------------------------


@pytest.mark.parametrize(
    ("name", "data", "fmt"),
    [
        ("a.pdf", samples.pdf(), Format.PDF),
        ("a.webp", samples.webp(), Format.WEBP),
        ("a.gif", samples.gif(samples.GIF87A), Format.GIF),
        ("a.GIF", samples.gif(samples.GIF89A), Format.GIF),
    ],
)
def test_v2_aceita_pela_assinatura(name: str, data: bytes, fmt: Format) -> None:
    assert classify(io.BytesIO(data), name, V2_FORMATS) is fmt


@pytest.mark.parametrize(
    ("fmt", "kind", "extension"),
    [
        (Format.PDF, Kind.DOCUMENT, ".pdf"),
        (Format.WEBP, Kind.IMAGE, ".webp"),
        (Format.GIF, Kind.IMAGE, ".gif"),
    ],
)
def test_formatos_novos_tem_tipo_e_extensao_canonica(
    fmt: Format, kind: Kind, extension: str
) -> None:
    assert (fmt.kind, fmt.extension) == (kind, extension)


def test_v2_continua_aceitando_os_formatos_da_v1() -> None:
    assert V1_FORMATS < V2_FORMATS
    assert classify(io.BytesIO(samples.png()), "a.png", V2_FORMATS) is Format.PNG
    assert classify(io.BytesIO(samples.text()), "a.txt", V2_FORMATS) is Format.TXT


def test_formato_binario_e_decidido_no_primeiro_bloco() -> None:
    class OneRead(io.BytesIO):
        reads = 0

        def read(self, size: int | None = -1) -> bytes:
            self.reads += 1
            assert self.reads == 1, "o classificador leu além do primeiro bloco"
            return super().read(size)

    data = samples.pdf() + b"\0" * (3 * TEXT_BLOCK_BYTES)
    assert classify(OneRead(data), "a.pdf", V2_FORMATS) is Format.PDF


def test_riff_que_nao_e_webp_e_recusado() -> None:
    wav = b"RIFF\x24\x00\x00\x00WAVEfmt "
    assert classify(io.BytesIO(wav), "a.webp", V2_FORMATS) == Refusal(
        "o conteúdo não tem a assinatura de WebP"
    )


def test_extensao_pdf_com_conteudo_jpeg_e_recusada() -> None:
    assert classify(io.BytesIO(samples.jpeg()), "a.pdf", V2_FORMATS) == Refusal(
        "o conteúdo é JPEG, mas a extensão é .pdf"
    )


def test_extensao_png_com_conteudo_gif_e_recusada_na_v2() -> None:
    assert classify(io.BytesIO(samples.gif()), "a.png", V2_FORMATS) == Refusal(
        "o conteúdo é GIF, mas a extensão é .png"
    )


def test_pdf_com_assinatura_de_executavel_e_recusado() -> None:
    assert classify(io.BytesIO(b"MZ%PDF-1.7"), "a.pdf", V2_FORMATS) == Refusal(
        "assinatura de executável ou script"
    )


def test_mensagem_da_v2_lista_as_extensoes_novas_no_fim() -> None:
    assert classify(io.BytesIO(samples.text()), "a.exe", V2_FORMATS) == Refusal(
        "extensão .exe não aceita; aceitas: .jpg, .jpeg, .png, .csv, .txt, .zip, .pdf, .webp, .gif"
    )


def test_v1_continua_recusando_pdf_com_a_mesma_mensagem() -> None:
    assert classify(io.BytesIO(samples.pdf()), "a.pdf", V1_FORMATS) == Refusal(
        "extensão .pdf não aceita; aceitas: .jpg, .jpeg, .png, .csv, .txt, .zip"
    )


def test_v1_nao_reconhece_pdf_dentro_de_png() -> None:
    assert classify(io.BytesIO(samples.pdf()), "a.png", V1_FORMATS) == Refusal(
        "o conteúdo não tem a assinatura de PNG"
    )
