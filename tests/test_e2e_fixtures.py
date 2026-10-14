"""Gerador de fixtures e conferência do pacote dos scripts de ponta a ponta (fatia 5, 3.9).

O gerador (`scripts/e2e_fixtures.py`) grava as entradas e o `expected.json`; o conferente
(`scripts/e2e_check_package.py`) é o que os scripts rodam no host, só com a biblioteca padrão.
Além de conferir cada fixture, estes testes rodam o executor de verdade sobre elas e passam o
ZIP gerado pelo conferente: o `expected.json` descreve o que o código faz, não um palpite.
"""

import hashlib
import io
import json
import shutil
import struct
import subprocess
import sys
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from goblinshift.processing.executor import execution
from goblinshift.runs.worker.collection import collect_outputs
from scripts import e2e_fixtures

REPO_ROOT = Path(__file__).resolve().parent.parent
CHECKER = REPO_ROOT / "scripts" / "e2e_check_package.py"
EXIF_ORIENTATION = 0x0112
GPS_IFD = 0x8825
PIXEL_LIMIT = 24_000_000
SMALL_FILE_BYTES = 64 * 1024


def generate(recipe: str, directory: Path) -> dict[str, Any]:
    e2e_fixtures.write(e2e_fixtures.build(recipe), directory)
    expected: dict[str, Any] = json.loads((directory / "expected.json").read_text())
    return expected


def fixture_bytes(directory: Path, expected: dict[str, Any], name: str) -> bytes:
    entry = next(e for e in expected["inputs"] if e["manifest"]["original_name"] == name)
    path: str = entry["path"]
    return (directory / path).read_bytes()


@pytest.mark.parametrize("recipe", e2e_fixtures.RECIPES)
def test_duas_geracoes_gravam_os_mesmos_bytes(recipe: str, tmp_path: Path) -> None:
    generate(recipe, tmp_path / "a")
    generate(recipe, tmp_path / "b")

    files_a = sorted(p.relative_to(tmp_path / "a") for p in (tmp_path / "a").rglob("*"))
    files_b = sorted(p.relative_to(tmp_path / "b") for p in (tmp_path / "b").rglob("*"))
    assert files_a == files_b
    for relative in files_a:
        if (tmp_path / "a" / relative).is_file():
            assert (tmp_path / "a" / relative).read_bytes() == (
                tmp_path / "b" / relative
            ).read_bytes(), relative


@pytest.mark.parametrize("recipe", e2e_fixtures.RECIPES)
def test_cada_entrada_fica_num_diretorio_proprio_com_o_nome_do_upload(
    recipe: str, tmp_path: Path
) -> None:
    # Foto.png e foto.PNG no mesmo diretório se sobrescreveriam no APFS, que ignora a caixa.
    expected = generate(recipe, tmp_path)

    for position, entry in enumerate(expected["inputs"]):
        path = tmp_path / entry["path"]
        manifest = entry["manifest"]
        assert manifest["position"] == position
        assert path.name == manifest["original_name"]
        assert path.parent.name == str(position)
        data = path.read_bytes()
        assert manifest["size"] == len(data)
        assert manifest["sha256"] == hashlib.sha256(data).hexdigest()


def test_receita_desconhecida_e_recusada() -> None:
    with pytest.raises(ValueError, match="csv-janitor"):
        e2e_fixtures.build("csv-janitor")


# Image Forge


@pytest.fixture
def forge(tmp_path: Path) -> Callable[[str], bytes]:
    expected = generate("image-forge", tmp_path)
    return lambda name: fixture_bytes(tmp_path, expected, name)


def test_jpeg_display_p3_tem_orientacao_6_e_gps(forge: Callable[[str], bytes]) -> None:
    with Image.open(io.BytesIO(forge("retrato-p3.jpg"))) as image:
        exif = image.getexif()
        assert image.format == "JPEG"
        assert b"Display P3" in image.info["icc_profile"].replace(b"\0", b"")
        assert exif[EXIF_ORIENTATION] == 6
        assert exif.get_ifd(GPS_IFD)


def test_mpo_tem_dois_quadros(forge: Callable[[str], bytes]) -> None:
    with Image.open(io.BytesIO(forge("celular.jpg"))) as image:
        assert image.format == "MPO"
        assert getattr(image, "n_frames", 1) == 2


def test_png_transparente_e_rgba(forge: Callable[[str], bytes]) -> None:
    with Image.open(io.BytesIO(forge("transparente.png"))) as image:
        assert (image.format, image.mode) == ("PNG", "RGBA")


def ihdr_of(png_bytes: bytes) -> tuple[int, int, int, int]:
    """Largura, altura, profundidade e tipo de cor lidos direto do IHDR."""
    assert png_bytes[12:16] == b"IHDR"
    width, height, bit_depth, color_type = struct.unpack_from(">IIBB", png_bytes, 16)
    return width, height, bit_depth, color_type


def test_png_de_16_bits_e_rgb_com_16_bits_no_ihdr(forge: Callable[[str], bytes]) -> None:
    _, _, bit_depth, color_type = ihdr_of(forge("profundo-16bits.png"))
    assert (bit_depth, color_type) == (16, 2)


def test_bomba_declara_mais_pixels_que_o_teto_em_poucos_bytes(
    forge: Callable[[str], bytes],
) -> None:
    data = forge("bomba.png")
    width, height, _, _ = ihdr_of(data)
    assert width * height > PIXEL_LIMIT
    assert len(data) < SMALL_FILE_BYTES


# Downloads Detox


@pytest.fixture
def detox(tmp_path: Path) -> tuple[Path, dict[str, Any]]:
    return tmp_path, generate("downloads-detox", tmp_path)


def test_detox_tem_oito_entradas(detox: tuple[Path, dict[str, Any]]) -> None:
    _, expected = detox
    assert len(expected["inputs"]) == 8


def test_ferramenta_zip_e_um_jar(detox: tuple[Path, dict[str, Any]]) -> None:
    directory, expected = detox
    data = fixture_bytes(directory, expected, "ferramenta.zip")
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        manifest = archive.read("META-INF/MANIFEST.MF")
        names = archive.namelist()
    assert b"Main-Class:" in manifest
    assert any(name.endswith(".class") for name in names)


def test_par_em_colisao_difere_so_na_caixa_e_no_conteudo(
    detox: tuple[Path, dict[str, Any]],
) -> None:
    directory, expected = detox
    upper, lower = "Foto.png", "foto.PNG"
    assert upper != lower
    assert upper.casefold() == lower.casefold()
    assert fixture_bytes(directory, expected, upper) != fixture_bytes(directory, expected, lower)


def test_ha_exatamente_um_par_de_copias_exatas(detox: tuple[Path, dict[str, Any]]) -> None:
    _, expected = detox
    digests = [entry["manifest"]["sha256"] for entry in expected["inputs"]]
    repeated = [d for d in set(digests) if digests.count(d) > 1]
    assert len(repeated) == 1
    assert digests.count(repeated[0]) == 2


def test_pagina_txt_e_html(detox: tuple[Path, dict[str, Any]]) -> None:
    directory, expected = detox
    text = fixture_bytes(directory, expected, "pagina.txt").decode()
    assert text.lstrip().casefold().startswith("<!doctype html")


# O executor real sobre as fixtures, conferido pelo mesmo conferente dos scripts.


def run_executor(fixtures: Path, expected: dict[str, Any], work: Path) -> Path:
    """Monta o work_dir que o worker montaria e roda o executor no próprio processo."""
    (work / "inputs").mkdir(parents=True)
    job_inputs = []
    for entry in expected["inputs"]:
        manifest = entry["manifest"]
        name = str(manifest["position"])
        shutil.copyfile(fixtures / entry["path"], work / "inputs" / name)
        job_inputs.append(
            {
                "position": manifest["position"],
                "path": name,
                "original_name": manifest["original_name"],
                "size": manifest["size"],
                "sha256": manifest["sha256"],
            }
        )
    job = {
        "recipe_id": expected["recipe_id"],
        "recipe_version": expected["recipe_version"],
        "parameters": expected["parameters"],
        "inputs": job_inputs,
    }
    (work / "job.json").write_text(json.dumps(job))
    (work / "context.json").write_text(json.dumps({"executor_image": None}))
    assert execution.main([str(work)]) == execution.ExitCode.SUCCEEDED
    return work / "out"


def check(package: Path, expected_json: Path, fixtures: Path) -> subprocess.CompletedProcess[str]:
    # -I, como nos scripts: sem site-packages, sem o diretório atual no caminho de import.
    return subprocess.run(  # noqa: S603 - argumentos montados aqui, sem shell
        [sys.executable, "-I", str(CHECKER), str(package), str(expected_json), str(fixtures)],
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize("recipe", e2e_fixtures.RECIPES)
def test_o_pacote_do_executor_real_passa_no_conferente(recipe: str, tmp_path: Path) -> None:
    expected = generate(recipe, tmp_path / "fixtures")
    out = run_executor(tmp_path / "fixtures", expected, tmp_path / "work")

    result = check(
        out / f"{recipe}.zip", tmp_path / "fixtures" / "expected.json", tmp_path / "fixtures"
    )

    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("recipe", e2e_fixtures.RECIPES)
def test_estado_esperado_e_o_que_a_coleta_do_worker_decide(recipe: str, tmp_path: Path) -> None:
    expected = generate(recipe, tmp_path / "fixtures")
    out = run_executor(tmp_path / "fixtures", expected, tmp_path / "work")

    collected = collect_outputs(out, len(expected["inputs"]), recipe_id=recipe)

    assert collected.report.outcome.value == expected["status"]


@pytest.mark.parametrize("recipe", e2e_fixtures.RECIPES)
def test_duas_execucoes_do_executor_dao_o_mesmo_zip(recipe: str, tmp_path: Path) -> None:
    expected = generate(recipe, tmp_path / "fixtures")
    first = run_executor(tmp_path / "fixtures", expected, tmp_path / "w1") / f"{recipe}.zip"
    second = run_executor(tmp_path / "fixtures", expected, tmp_path / "w2") / f"{recipe}.zip"

    assert first.read_bytes() == second.read_bytes()


def rewritten_zip(source: Path, target: Path, order: Callable[[list[str]], list[str]]) -> None:
    with zipfile.ZipFile(source) as original, zipfile.ZipFile(target, "w") as copy:
        for name in order(original.namelist()):
            copy.writestr(original.getinfo(name), original.read(name))


@pytest.mark.parametrize(
    "tamper",
    [
        pytest.param(lambda names: list(reversed(names)), id="ordem-invertida"),
        pytest.param(lambda names: names[1:], id="entrada-faltando"),
    ],
)
def test_conferente_recusa_zip_diferente_do_esperado(
    tamper: Callable[[list[str]], list[str]], tmp_path: Path
) -> None:
    expected = generate("downloads-detox", tmp_path / "fixtures")
    out = run_executor(tmp_path / "fixtures", expected, tmp_path / "work")
    tampered = tmp_path / "tampered.zip"
    rewritten_zip(out / "downloads-detox.zip", tampered, tamper)

    result = check(tampered, tmp_path / "fixtures" / "expected.json", tmp_path / "fixtures")

    assert result.returncode != 0
    assert "entradas do ZIP" in result.stderr


def test_conferente_recusa_saida_com_bytes_diferentes_do_upload(tmp_path: Path) -> None:
    expected = generate("downloads-detox", tmp_path / "fixtures")
    out = run_executor(tmp_path / "fixtures", expected, tmp_path / "work")
    (tmp_path / "fixtures" / expected["inputs"][0]["path"]).write_bytes(b"outro conteudo")

    result = check(
        out / "downloads-detox.zip", tmp_path / "fixtures" / "expected.json", tmp_path / "fixtures"
    )

    assert result.returncode != 0
