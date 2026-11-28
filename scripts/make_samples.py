"""Gera os arquivos de exemplo da Bancada ("Testar com arquivo de exemplo", D4).

Uma amostra por receita, em `web/src/workbench/samples/`, feita pelo próprio projeto (CC0,
registrada em `web/LICENCAS-FONTES.md`) e com no máximo 200 KB cada. Tudo sai determinístico:
datas fixas no ZIP, `/ID` estável no PDF, imagem a partir de uma arte já versionada e vídeo com
`bitexact` e uma thread só. `--check` gera de novo numa pasta temporária e compara os bytes com os
versionados (o vídeo fica de fora: o ffmpeg muda o fluxo entre versões, então ele é conferido pelo
`ffprobe` em `tests/test_make_samples.py`).

Uso: `python -m scripts.make_samples [--check]`.
"""

from __future__ import annotations

import argparse
import io
import subprocess
import sys
import tempfile
import zipfile
from collections.abc import Callable, Mapping
from pathlib import Path

import pikepdf
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SAMPLES_DIR = ROOT / "web" / "src" / "workbench" / "samples"
ART_SOURCE = ROOT / "web" / "src" / "assets" / "art" / "goblins" / "rubia-1280w.webp"

MAX_SAMPLE_BYTES = 200 * 1024
VIDEO_NAME = "exemplo-clipe.webm"
IMAGE_SIZE = (2400, 1600)
# Fundo da oficina: madeira escura em degradê vertical (tons próximos da paleta, ADR 032).
BACKGROUND_TOP = (58, 40, 28)
BACKGROUND_BOTTOM = (28, 19, 13)
# Data fixa das entradas do ZIP: sem ela o arquivo muda a cada geração.
ZIP_DATE = (2026, 11, 22, 12, 0, 0)

CSV_SAMPLE = (
    "nome,email,cidade,observacao\r\n"
    "  Ana Souza ,ana@example.com,Recife,\r\n"
    "Bruno Lima,  bruno@example.com,Curitiba ,\r\n"
    "  Ana Souza ,ana@example.com,Recife,\r\n"
    "Carla Dias,carla@example.com,  Belém,\r\n"
    "Diego Rocha ,diego@example.com,Manaus,\r\n"
    "Bruno Lima,  bruno@example.com,Curitiba ,\r\n"
    "Elisa Prado,elisa@example.com, Porto Alegre ,\r\n"
).encode()

README_TXT = (
    "Pasta de downloads de exemplo do GoblinShift.\n"
    "Tem uma planilha repetida com outro nome: o Downloads Detox acha e tira a cópia.\n"
).encode()

INVENTORY_CSV = b"item,quantidade\r\nmartelo,3\r\nengrenagem,12\r\nlanterna,1\r\n"


def csv_sample() -> bytes:
    """Clientes com espaços nas pontas, linhas duplicadas e a coluna `observacao` vazia."""
    return CSV_SAMPLE


def _gradient_background(size: tuple[int, int]) -> Image.Image:
    width, height = size
    column = Image.new("RGB", (1, height))
    for y in range(height):
        weight = y / (height - 1)
        column.putpixel(
            (0, y),
            tuple(
                round(top + (bottom - top) * weight)
                for top, bottom in zip(BACKGROUND_TOP, BACKGROUND_BOTTOM, strict=True)
            ),
        )
    return column.resize((width, height), Image.Resampling.NEAREST)


def jpeg_sample(art: Path = ART_SOURCE, quality: int = 80) -> bytes:
    """A Rúbia (arte já versionada do elenco) sobre o fundo da oficina, JPEG de 2400 por 1600."""
    canvas = _gradient_background(IMAGE_SIZE)
    with Image.open(art) as source:
        goblin = source.convert("RGBA")
    scale = IMAGE_SIZE[1] / goblin.height
    goblin = goblin.resize((round(goblin.width * scale), IMAGE_SIZE[1]), Image.Resampling.LANCZOS)
    left = (IMAGE_SIZE[0] - goblin.width) // 2
    canvas.paste(goblin, (left, 0), goblin)
    buffer = io.BytesIO()
    canvas.save(buffer, "JPEG", quality=quality, optimize=True)
    return buffer.getvalue()


def _png_bytes() -> bytes:
    image = Image.new("RGB", (320, 200), BACKGROUND_TOP)
    for x in range(40, 280):
        for y in range(60, 140):
            image.putpixel((x, y), (184, 115, 51))
    buffer = io.BytesIO()
    image.save(buffer, "PNG", optimize=True)
    return buffer.getvalue()


def _small_jpeg_bytes() -> bytes:
    image = Image.new("RGB", (480, 320), BACKGROUND_BOTTOM)
    for x in range(120, 360):
        for y in range(80, 240):
            image.putpixel((x, y), (122, 140, 84))
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=80)
    return buffer.getvalue()


def _zip_bytes() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        entry = zipfile.ZipInfo("ferramentas.txt", date_time=ZIP_DATE)
        entry.compress_type = zipfile.ZIP_DEFLATED
        entry.external_attr = 0o644 << 16
        entry.create_system = 3
        archive.writestr(entry, "martelo\nengrenagem\nlanterna\n")
    return buffer.getvalue()


def downloads_sample() -> dict[str, bytes]:
    """Seis arquivos mistos de uma pasta de downloads, um deles cópia de outro com outro nome."""
    inventory = INVENTORY_CSV
    return {
        "IMG_2041.JPG": _small_jpeg_bytes(),
        "captura-de-tela.png": _png_bytes(),
        "estoque.csv": inventory,
        "estoque(1).csv": inventory,
        "leia-me.txt": README_TXT,
        "ferramentas.zip": _zip_bytes(),
    }


def pdf_sample(title: str, pages: int) -> bytes:
    """PDF de `pages` páginas A5 com um título por página, em Helvetica (fonte padrão do PDF)."""
    pdf = pikepdf.new()
    font = pdf.make_indirect(
        pikepdf.Dictionary(
            Type=pikepdf.Name.Font,
            Subtype=pikepdf.Name.Type1,
            BaseFont=pikepdf.Name.Helvetica,
        )
    )
    for number in range(1, pages + 1):
        text = f"{title} - pagina {number} de {pages}"
        stream = f"BT /F1 24 Tf 48 520 Td ({text}) Tj ET".encode("latin-1")
        page = pikepdf.Page(
            pikepdf.Dictionary(
                Type=pikepdf.Name.Page,
                MediaBox=[0, 0, 420, 595],
                Resources=pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=font)),
                Contents=pdf.make_stream(stream),
            )
        )
        pdf.pages.append(page)
    buffer = io.BytesIO()
    pdf.save(buffer, deterministic_id=True)
    return buffer.getvalue()


def static_samples() -> dict[str, bytes]:
    """Todas as amostras menos o vídeo, por caminho relativo a `SAMPLES_DIR`."""
    samples = {
        "exemplo-clientes.csv": csv_sample(),
        "exemplo-oficina.jpg": jpeg_sample(),
        "exemplo-a.pdf": pdf_sample("Exemplo A", 2),
        "exemplo-b.pdf": pdf_sample("Exemplo B", 3),
    }
    samples.update({f"exemplo-downloads/{name}": data for name, data in downloads_sample().items()})
    return samples


def make_video(target: Path, run: Callable[..., object] = subprocess.run) -> None:
    """Clipe de 3 s (`testsrc` + seno) em WebM VP8/Opus, com `bitexact` e uma thread."""
    run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=3:size=320x240:rate=24",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=3:sample_rate=48000",
            "-map_metadata",
            "-1",
            "-c:v",
            "libvpx",
            "-b:v",
            "250k",
            "-c:a",
            "libopus",
            "-b:a",
            "48k",
            "-threads",
            "1",
            "-fflags",
            "+bitexact",
            "-flags:v",
            "+bitexact",
            "-flags:a",
            "+bitexact",
            str(target),
        ],
        check=True,
    )


def write_samples(target: Path, samples: Mapping[str, bytes]) -> None:
    for relative, data in samples.items():
        path = target / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


def drift(target: Path, samples: Mapping[str, bytes]) -> list[str]:
    """Amostras versionadas que não batem com as geradas agora (ausentes ou com outros bytes)."""
    return sorted(
        relative
        for relative, data in samples.items()
        if not (target / relative).is_file() or (target / relative).read_bytes() != data
    )


def oversized(samples: Mapping[str, bytes]) -> list[str]:
    return sorted(name for name, data in samples.items() if len(data) > MAX_SAMPLE_BYTES)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument(
        "--check", action="store_true", help="só confere os bytes versionados, sem gravar"
    )
    args = parser.parse_args(argv)
    samples = static_samples()
    too_big = oversized(samples)
    if too_big:
        print(f"Amostras acima de 200 KB: {', '.join(too_big)}", file=sys.stderr)
        return 1
    if args.check:
        stale = drift(SAMPLES_DIR, samples)
        if stale:
            print(f"Amostras diferentes do gerador: {', '.join(stale)}", file=sys.stderr)
            return 1
        return 0
    write_samples(SAMPLES_DIR, samples)
    with tempfile.TemporaryDirectory() as scratch:
        video = Path(scratch) / VIDEO_NAME
        make_video(video)
        (SAMPLES_DIR / VIDEO_NAME).write_bytes(video.read_bytes())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
