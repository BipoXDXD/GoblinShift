"""Nome original como dado: radical e extensão seguros para virar componente (3.6)."""

import unicodedata

import pytest

from goblinshift.processing.names import MAX_EXTENSION_BYTES, extension_of, safe_stem

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
