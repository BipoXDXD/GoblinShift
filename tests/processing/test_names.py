"""Nome original como dado: radical e extensão seguros para virar componente (3.6)."""

import unicodedata

import pytest
from hypothesis import given
from hypothesis import strategies as st

from goblinshift.processing.names import (
    MAX_EXTENSION_BYTES,
    extension_of,
    has_unsafe_char,
    safe_stem,
)

FALLBACK = "arquivo"


@pytest.mark.parametrize(
    ("original", "expected"),
    [
        ("foto.jpg", "foto"),
        ("vendas.2026.csv", "vendas.2026"),
        ("sem-extensao", "sem-extensao"),
        ("a/b.png", "a_b"),
        ("a\\b.png", "a_b"),
        ("a\x00b.png", "a_b"),
        ("a\nb\x7f.png", "a_b_"),
        (".bashrc", "bashrc"),
        ("...png", FALLBACK),
        ("..", FALLBACK),
        (".", FALLBACK),
        ("", FALLBACK),
        ("/", "_"),
    ],
)
def test_radical_seguro(original: str, expected: str) -> None:
    assert safe_stem(original, FALLBACK) == expected


def test_caminho_vira_um_componente_so() -> None:
    original = "../../etc/passwd"

    assert safe_stem(original, FALLBACK) + extension_of(original) == "_.._etc_passwd"


def test_radical_sai_em_nfc() -> None:
    nfd = unicodedata.normalize("NFD", "Relatório.txt")

    assert safe_stem(nfd, FALLBACK) == unicodedata.normalize("NFC", "Relatório")


def test_radical_nunca_tem_separador_nem_controle_nem_ponto_inicial() -> None:
    stem = safe_stem(".\x01/..\\x.y", FALLBACK)

    assert not stem.startswith(".")
    assert "/" not in stem
    assert "\\" not in stem
    assert all(unicodedata.category(char) != "Cc" for char in stem)


@pytest.mark.parametrize(
    ("original", "expected"),
    [
        ("foto.JPG", ".JPG"),
        ("foto.jpeg", ".jpeg"),
        ("vendas.2026.csv", ".csv"),
        ("sem-extensao", ""),
        (".bashrc", ""),
        ("termina-em-ponto.", ""),
        ("", ""),
        ("a.b/c", ".b_c"),
        ("a.c\x00", ".c_"),
    ],
)
def test_extensao_com_ponto_e_caixa_original(original: str, expected: str) -> None:
    assert extension_of(original) == expected


def test_extensao_no_teto_de_bytes_e_mantida() -> None:
    extension = "." + "x" * (MAX_EXTENSION_BYTES - 1)

    assert extension_of("a" + extension) == extension


def test_extensao_acima_do_teto_nao_e_extensao() -> None:
    # Uma "extensão" gigante vinda do usuário não pode impedir o nome de caber em 180 bytes.
    assert extension_of("a." + "x" * MAX_EXTENSION_BYTES) == ""


def test_radical_mais_extensao_reconstroem_um_nome_comum() -> None:
    original = "Foto de Férias.PNG"

    assert safe_stem(original, FALLBACK) + extension_of(original) == original


# --- Formato invisível (Unicode Cf): trocado por `_`, como os controles ---------------------
# U+202E (RLO) exibiria "fatura\u202egpj.exe" como "fatura" + "exe.jpg" ao contrário; ZWSP e BOM
# no meio criam nomes que parecem iguais; os isolates (U+2066 a U+2069) reordenam a exibição.

INVISIBLE_FORMAT = {
    "rlo": "\u202e",
    "zwsp": "\u200b",
    "bom": "\ufeff",
    "lri": "\u2066",
    "rli": "\u2067",
    "fsi": "\u2068",
    "pdi": "\u2069",
}


@pytest.mark.parametrize("char", INVISIBLE_FORMAT.values(), ids=INVISIBLE_FORMAT.keys())
def test_formato_invisivel_no_radical_vira_sublinhado(char: str) -> None:
    assert safe_stem(f"fatura{char}gpj.exe", FALLBACK) == "fatura_gpj"


@pytest.mark.parametrize("char", INVISIBLE_FORMAT.values(), ids=INVISIBLE_FORMAT.keys())
def test_formato_invisivel_na_extensao_vira_sublinhado(char: str) -> None:
    assert extension_of(f"foto.jp{char}g") == ".jp_g"


def test_radical_so_de_formato_invisivel_nao_vira_vazio() -> None:
    assert safe_stem("\u200b\u200b.txt", FALLBACK) == "__"


@pytest.mark.parametrize(
    ("text", "expected"),
    [("ok.txt", False), ("a\u202eb", True), ("a\x01b", True), ("Relatório", False)],
)
def test_has_unsafe_char_cobre_controle_e_formato(text: str, expected: bool) -> None:
    assert has_unsafe_char(text) is expected


@given(st.text())
def test_radical_e_extensao_nunca_tem_controle_formato_nem_separador(original: str) -> None:
    pieces = safe_stem(original, FALLBACK) + extension_of(original)

    assert not has_unsafe_char(pieces)
    assert "/" not in pieces
    assert "\\" not in pieces
