"""Amostras da Bancada ("Testar com arquivo de exemplo", D4): bytes versionados e conteúdo."""

import csv
import hashlib
import io
import json
import subprocess
from collections import Counter

import pikepdf
from PIL import Image

from scripts.make_samples import (
    MAX_SAMPLE_BYTES,
    SAMPLES_DIR,
    VIDEO_NAME,
    drift,
    main,
    static_samples,
)


def test_gerar_de_novo_nao_muda_os_bytes_versionados() -> None:
    assert drift(SAMPLES_DIR, static_samples()) == []
    assert main(["--check"]) == 0


def test_toda_amostra_versionada_tem_no_maximo_200_kb() -> None:
    files = [path for path in SAMPLES_DIR.rglob("*") if path.is_file()]

    assert files
    assert [path.name for path in files if path.stat().st_size > MAX_SAMPLE_BYTES] == []


def test_csv_tem_espacos_nas_pontas_duplicatas_e_uma_coluna_vazia() -> None:
    rows = list(csv.reader(io.StringIO((SAMPLES_DIR / "exemplo-clientes.csv").read_text())))
    header, body = rows[0], rows[1:]

    assert any(cell != cell.strip() for row in body for cell in row)
    assert max(Counter(tuple(row) for row in body).values()) > 1
    empty = [name for index, name in enumerate(header) if all(row[index] == "" for row in body)]
    assert empty == ["observacao"]


def test_imagem_tem_2400_por_1600() -> None:
    with Image.open(SAMPLES_DIR / "exemplo-oficina.jpg") as image:
        assert (image.format, image.size) == ("JPEG", (2400, 1600))


def test_pdfs_tem_2_e_3_paginas() -> None:
    pages = {}
    for name in ("exemplo-a.pdf", "exemplo-b.pdf"):
        with pikepdf.open(SAMPLES_DIR / name) as pdf:
            pages[name] = len(pdf.pages)

    assert pages == {"exemplo-a.pdf": 2, "exemplo-b.pdf": 3}


def test_downloads_tem_seis_arquivos_e_so_um_repetido() -> None:
    files = sorted((SAMPLES_DIR / "exemplo-downloads").iterdir())
    digests = Counter(hashlib.sha256(path.read_bytes()).hexdigest() for path in files)

    assert len(files) == 6
    assert sorted(digests.values()) == [1, 1, 1, 1, 2]


def test_video_tem_3_s_em_webm_vp8_e_opus() -> None:
    probe = subprocess.run(  # noqa: S603 - argumentos montados pelo teste, sem shell
        [  # noqa: S607 - o ffprobe do PATH, como no job do CI
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=format_name,duration:stream=codec_name",
            "-of",
            "json",
            str(SAMPLES_DIR / VIDEO_NAME),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    info = json.loads(probe.stdout)

    assert "webm" in info["format"]["format_name"]
    assert abs(float(info["format"]["duration"]) - 3.0) < 0.1
    assert sorted(stream["codec_name"] for stream in info["streams"]) == ["opus", "vp8"]
