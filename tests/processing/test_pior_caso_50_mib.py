"""Pior caso de cada receita com entradas no teto de 50 MiB (ADR 062).

Testes de guarda: o executor não lê o teto do envio, então já passavam antes de ele subir. Existem
para o teto não subir de novo sem medição. Cada receita roda num processo novo, que mede o próprio
pico de RSS (`tests/processing/peak_rss.py`); os `heavy` ficam fora do padrão local e rodam no job
Verificações da CI.
"""

import functools
import hashlib
import os
from collections.abc import Sequence
from pathlib import Path

import pikepdf
import pytest
from PIL import Image

from executor_ctl.profile import MEMORY_BYTES
from goblinshift.catalog.versions import CATALOG
from goblinshift.files.limits import MAX_FILE_BYTES
from goblinshift.processing.csv_janitor import Limits as TableLimits
from goblinshift.processing.output_dir import FileSystemOutputDir
from goblinshift.processing.recipe import (
    InputFile,
    ItemDuplicate,
    ItemOk,
    ItemRejected,
    ItemRejectionCode,
)
from goblinshift.processing.registry import RECIPES
from tests.processing.peak_rss import run_measuring_peak_rss

MIB = 1024 * 1024
# Folga de 1/4 do limite do contêiner para o interpretador, a biblioteca e a fragmentação.
MEMORY_BUDGET = MEMORY_BYTES * 3 // 4
# Quem processa em fluxo não pode acrescentar ao processo nem metade de uma entrada no teto.
STREAMING_GROWTH_BUDGET = MAX_FILE_BYTES // 2
# Abaixo dos 24 MP do Image Forge (4898² = 23.990.404 px).
IMAGE_SIDE = 4898

type Outcome = tuple[str, str]


class _IgnoredProgress:
    def item_done(self, position: int) -> None:
        pass


def run_recipe(recipe_id: str, inputs: Sequence[InputFile], out: Path) -> list[Outcome]:
    """Roda a v1 com os parâmetros padrão do catálogo, como o executor; devolve o desfecho de cada
    item (tipo e código), que serializa de volta para o processo do teste."""
    out.mkdir()
    raw_params = CATALOG.resolve(recipe_id, 1, {})
    report = RECIPES[recipe_id, 1].execute(
        raw_params, inputs, FileSystemOutputDir(out), _IgnoredProgress()
    )
    outcomes: list[Outcome] = []
    for item in report.items:
        match item:
            case ItemOk():
                outcomes.append(("ok", ""))
            case ItemRejected(code=code):
                outcomes.append(("rejected", code))
            case ItemDuplicate():
                outcomes.append(("duplicate", ""))
    return outcomes


def input_files(paths: Sequence[Path], names: Sequence[str]) -> list[InputFile]:
    files = []
    for position, (path, name) in enumerate(zip(paths, names, strict=True)):
        with path.open("rb") as source:
            digest = hashlib.file_digest(source, "sha256").hexdigest()
        files.append(InputFile(position, path, name, path.stat().st_size, digest))
    return files


def write_noisy_png(path: Path, side: int) -> Path:
    """RGBA (o modo aceito de mais bytes por pixel) com 3 bits aleatórios em cada byte.

    O deflate guarda cerca de 3 bits por byte, e o PNG de 4898 x 4898 fica perto de 47 MiB. Ruído
    em todos os pixels, e não ruído pleno numa parte e zeros no resto: o macOS comprime páginas
    de zeros na memória e o pico medido escondia metade da imagem decodificada.
    """
    noise = os.urandom(side * side * 4).translate(bytes(range(8)) * 32)
    Image.frombytes("RGBA", (side, side), noise).save(path)
    return path


def write_pdf_with_random_stream(path: Path, stream_bytes: int) -> Path:
    """Uma página que usa uma imagem RGB crua de bytes aleatórios, sem compressão."""
    width = 4096
    height = stream_bytes // (width * 3)
    pdf = pikepdf.new()
    image = pdf.make_stream(os.urandom(width * height * 3))
    image.Type = pikepdf.Name.XObject
    image.Subtype = pikepdf.Name.Image
    image.Width, image.Height = width, height
    image.ColorSpace = pikepdf.Name.DeviceRGB
    image.BitsPerComponent = 8
    pdf.add_blank_page(page_size=(612, 792))
    page = pdf.pages[0]
    page.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Im0=image))
    page.Contents = pdf.make_stream(b"q 612 0 0 792 0 0 cm /Im0 Do Q")
    pdf.save(path, compress_streams=False)
    return path


def assert_near_the_ceiling(path: Path) -> None:
    assert 45 * MIB < path.stat().st_size <= MAX_FILE_BYTES


@pytest.mark.heavy
def test_image_forge_com_png_de_50_mib_cabe_na_memoria(tmp_path: Path) -> None:
    source = write_noisy_png(tmp_path / "foto.png", IMAGE_SIDE)
    assert_near_the_ceiling(source)
    inputs = input_files([source], ["foto.png"])

    measured = run_measuring_peak_rss(
        functools.partial(run_recipe, "image-forge", inputs, tmp_path / "out")
    )

    assert measured.result == [("ok", "")]
    assert measured.peak_bytes < MEMORY_BUDGET


@pytest.mark.heavy
def test_pdf_press_junta_dois_pdfs_de_50_mib_na_memoria(tmp_path: Path) -> None:
    sources = [
        write_pdf_with_random_stream(tmp_path / f"{name}.pdf", 48 * MIB) for name in ("a", "b")
    ]
    for source in sources:
        assert_near_the_ceiling(source)
    inputs = input_files(sources, ["a.pdf", "b.pdf"])

    measured = run_measuring_peak_rss(
        functools.partial(run_recipe, "pdf-press", inputs, tmp_path / "out")
    )

    assert measured.result == [("ok", ""), ("ok", "")]
    assert measured.peak_bytes < MEMORY_BUDGET


@pytest.mark.heavy
def test_detox_copia_nove_arquivos_de_50_mib_sem_carregar_em_memoria(tmp_path: Path) -> None:
    # 9 x 50 MiB = 450 MiB, dentro dos 480 MiB do lote do Detox. Texto, que o Detox lê até o fim
    # para classificar antes de copiar; a primeira linha distingue os arquivos (nenhuma duplicata).
    line = "linha de texto com acentuação\n".encode()
    block = line * (MIB // len(line))
    paths = []
    for index in range(9):
        path = tmp_path / f"notas-{index}.txt"
        first = f"arquivo {index}\n".encode()
        with path.open("wb") as sink:
            sink.write(first)
            while sink.tell() + len(block) < MAX_FILE_BYTES:
                sink.write(block)
            sink.write(b"x" * (MAX_FILE_BYTES - sink.tell() - 1) + b"\n")
        assert path.stat().st_size == MAX_FILE_BYTES
        paths.append(path)
    inputs = input_files(paths, [path.name for path in paths])

    measured = run_measuring_peak_rss(
        functools.partial(run_recipe, "downloads-detox", inputs, tmp_path / "out")
    )

    assert measured.result == [("ok", "")] * 9
    assert measured.growth_bytes < STREAMING_GROWTH_BUDGET


@pytest.mark.heavy
def test_csv_de_50_mib_e_rejeitado_lendo_so_o_limite_da_tabela(tmp_path: Path) -> None:
    row = b"nome,cidade\r\n"
    source = tmp_path / "grande.csv"
    with source.open("wb") as sink:
        for _ in range(MAX_FILE_BYTES // MIB):
            sink.write(row * (MIB // len(row)))
    assert_near_the_ceiling(source)
    inputs = input_files([source], ["grande.csv"])

    measured = run_measuring_peak_rss(
        functools.partial(run_recipe, "csv-janitor", inputs, tmp_path / "out")
    )

    assert measured.result == [("rejected", ItemRejectionCode.FIELD_LIMIT_EXCEEDED)]
    assert measured.growth_bytes < 2 * TableLimits().table_bytes
