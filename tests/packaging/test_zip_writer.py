"""Compressão por entrada do ZIP (3.6): já comprimido vai sem DEFLATE."""

import zipfile

import pytest

from goblinshift.processing.packaging import compression_for


@pytest.mark.parametrize(
    "arcname",
    [
        "images/foto.jpg",
        "images/foto.JPG",
        "images/foto.jpeg",
        "images/foto.JPEG",
        "thumbnails/foto.png",
        "images/foto.Png",
        "archives/pacote.zip",
        "pacote.ZIP",
    ],
)
def test_formatos_ja_comprimidos_vao_sem_compressao(arcname: str) -> None:
    assert compression_for(arcname) == zipfile.ZIP_STORED


@pytest.mark.parametrize(
    "arcname",
    [
        "tables/a.csv",
        "text/a.txt",
        "manifest.json",
        "report.json",
        "sem-extensao",
        "images/foto.jpg.txt",
        "archives/.zip",
        "foto.jpgx",
    ],
)
def test_o_resto_vai_com_deflate(arcname: str) -> None:
    assert compression_for(arcname) == zipfile.ZIP_DEFLATED
