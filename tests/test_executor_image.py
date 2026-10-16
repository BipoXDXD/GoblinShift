"""Imagem do executor (docker/executor.Dockerfile): conteúdo mínimo, usuário e contrato do /work.

Constrói a imagem localmente e a roda com o mesmo perfil fixo do executor-ctl
(docs/GoblinShiftPlan.md, 3.6; contrato da fatia 4). O runtime vem de `GS_TEST_RUNTIME`: `runc`
no Mac e nos testes comuns, `runsc` no job `gvisor` da CI. Sem Docker, o módulo inteiro é pulado.
"""

import csv
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import zipfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from goblinshift.catalog import CATALOG
from tests.downloads_detox import samples
from tests.image_forge import media
from tests.image_forge.icc import DISPLAY_P3, rgb_profile
from tests.media_mill import media as video
from tests.pdf_press import samples as pdf_samples

ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = ROOT / "docker" / "executor.Dockerfile"
# Uma tag por checkout: worktrees de branches diferentes rodando a suíte ao mesmo tempo não
# reconstroem a imagem uns dos outros no meio dos testes.
IMAGE = f"goblinshift-executor:test-{hashlib.sha256(bytes(ROOT)).hexdigest()[:12]}"
RUNTIME = os.environ.get("GS_TEST_RUNTIME", "runc")
EXECUTOR_UID = 61000
EXECUTOR_GID = 61001
# O estágio do FFmpeg (ADR 036) compila do fonte quando não há cache: alguns minutos.
BUILD_TIMEOUT_SECONDS = 1200
RUN_TIMEOUT_SECONDS = 120
# O usuário do container não é o dono do diretório temporário do teste; na VPS o acesso vem do
# grupo gs-exec (modo 2770), que o teste não consegue criar sem root.
WORLD_WRITABLE_DIR = 0o777
WORLD_READABLE_DIR = 0o755
WORLD_READABLE_FILE = 0o644
SUCCEEDED = 0
FFMPEG_VERSION = "8.1.2"  # ADR 036; ARG FFMPEG_VERSION em docker/executor.Dockerfile
PILLOW_VERSION = "12.3.0"  # ADR 018; tests/test_dependencies.py confere o mesmo pin no lock
PIKEPDF_VERSION = "10.16.0"  # ADR 035; idem
QPDF_VERSION = "12.4.2"  # o libqpdf que vem dentro da wheel do pikepdf
RECIPE_FAILURE = 3
# Perfil de recursos do executor-ctl (1.7): 1 vCPU e 2 GiB, com swap igual à memória.
RESOURCE_LIMITS = ("--memory=2g", "--memory-swap=2g", "--cpus=1")

VALID = b"nome,cidade\r\nAna,Recife\r\n"
INVALID = b'nome,cidade\r\n"Ana,Recife\r\n'


def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    probe = subprocess.run(
        ["docker", "info", "--format", "{{.ServerVersion}}"],  # noqa: S607
        capture_output=True,
        check=False,
        timeout=RUN_TIMEOUT_SECONDS,
    )
    return probe.returncode == 0


pytestmark = [
    pytest.mark.executor_image,
    pytest.mark.skipif(not _docker_available(), reason="precisa de Docker"),
]


def docker(*args: str, timeout: int = RUN_TIMEOUT_SECONDS) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - argumentos montados pelo teste, sem shell
        ["docker", *args],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )


@pytest.fixture(scope="module")
def image() -> str:
    build = docker(
        "build", "--quiet", "--file", str(DOCKERFILE), "--tag", IMAGE, str(ROOT),
        timeout=BUILD_TIMEOUT_SECONDS,
    )  # fmt: skip
    assert build.returncode == 0, build.stderr
    return IMAGE


def fixed_profile(*extra: str) -> list[str]:
    """Opções do `docker run` do executor-ctl que valem para qualquer teste da imagem."""
    return [
        "run", "--rm", f"--runtime={RUNTIME}", "--network=none", "--read-only",
        "--cap-drop=ALL", "--security-opt=no-new-privileges", "--pull=never", *extra,
    ]  # fmt: skip


def run_python(image: str, code: str, *extra: str) -> subprocess.CompletedProcess[str]:
    return docker(*fixed_profile(*extra, "--entrypoint", "python", image, "-c", code))


def run_executor(image: str, work: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    mounts = [
        *extra,
        f"--user={EXECUTOR_UID}:{EXECUTOR_GID}",
        "--volume", f"{work}:/work",
        "--volume", f"{work / 'inputs'}:/work/inputs:ro",
    ]  # fmt: skip
    return docker(*fixed_profile(*mounts, image))


def make_work_dir(
    root: Path,
    tables: Sequence[tuple[str, bytes]],
    recipe_id: str = "csv-janitor",
    parameters: Mapping[str, object] | None = None,
) -> Path:
    """Área como o worker a monta, com os parâmetros resolvidos pelo catálogo."""
    work = root / "work"
    inputs = work / "inputs"
    inputs.mkdir(parents=True)
    entries = []
    for position, (original_name, data) in enumerate(tables):
        name = f"{position:04}.bin"
        (inputs / name).write_bytes(data)
        (inputs / name).chmod(WORLD_READABLE_FILE)
        entries.append(
            {
                "position": position,
                "path": name,
                "original_name": original_name,
                "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        )
    job = {
        "recipe_id": recipe_id,
        "recipe_version": 1,
        "parameters": CATALOG.resolve(recipe_id, 1, parameters or {}),
        "inputs": entries,
    }
    (work / "job.json").write_text(json.dumps(job), encoding="utf-8")
    (work / "job.json").chmod(WORLD_READABLE_FILE)
    inputs.chmod(WORLD_READABLE_DIR)
    work.chmod(WORLD_WRITABLE_DIR)
    return work


@pytest.mark.parametrize("module", ["sqlalchemy", "fastapi", "pydantic", "goblinshift.runs", "pip"])
def test_imagem_nao_consegue_importar_o_que_esta_fora_do_processing(
    image: str, module: str
) -> None:
    result = run_python(image, f"import {module}")

    assert result.returncode != 0
    assert "ModuleNotFoundError" in result.stderr


def test_executor_so_carrega_modulos_do_projeto_dentro_do_processing(image: str) -> None:
    code = (
        "import sys, goblinshift.processing.executor, goblinshift.processing.registry\n"
        "print('\\n'.join(sorted(m for m in sys.modules if m.startswith('goblinshift'))))"
    )

    result = run_python(image, code)

    assert result.returncode == 0, result.stderr
    loaded = result.stdout.split()
    assert "goblinshift.processing.executor" in loaded
    assert [
        m for m in loaded if m != "goblinshift" and not m.startswith("goblinshift.processing")
    ] == []


def test_executor_processa_um_work_dir_real(image: str, tmp_path: Path) -> None:
    work = make_work_dir(tmp_path, [("a.csv", VALID), ("b.csv", INVALID)])

    result = run_executor(image, work)

    assert result.returncode == SUCCEEDED, result.stderr
    report = json.loads((work / "out" / "report.json").read_text(encoding="utf-8"))
    assert report["summary"] == {
        "inputs": 2,
        "ok": 1,
        "rejected": 1,
        "duplicates": 0,
        "outputs": 1,
    }
    assert [item["status"] for item in report["items"]] == ["ok", "rejected"]
    cleaned = (work / "out" / "a.csv").read_text(encoding="utf-8")
    assert list(csv.reader(io.StringIO(cleaned))) == [["nome", "cidade"], ["Ana", "Recife"]]
    assert (work / "progress.jsonl").read_text(encoding="utf-8").count("\n") == len(report["items"])
    with zipfile.ZipFile(work / "out" / "csv-janitor.zip") as archive:
        assert archive.namelist() == ["a.csv", "manifest.json", "report.json"]
        assert archive.testzip() is None
    manifest = json.loads((work / "out" / "manifest.json").read_text(encoding="utf-8"))
    assert [entry["status"] for entry in manifest["inputs"]] == ["ok", "rejected"]


def test_pillow_fixado_e_a_versao_do_lock(image: str) -> None:
    result = run_python(image, "import PIL; print(PIL.__version__)")

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == PILLOW_VERSION


@pytest.mark.parametrize("feature", ["jpg", "zlib", "littlecms2"])
def test_pillow_traz_as_bibliotecas_nativas_que_o_image_forge_usa(image: str, feature: str) -> None:
    result = run_python(image, f"import PIL.features as f; print(f.check({feature!r}))")

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "True"


def test_pikepdf_e_qpdf_fixados_sao_as_versoes_do_lock(image: str) -> None:
    result = run_python(
        image, "import pikepdf; print(pikepdf.__version__, pikepdf.__libqpdf_version__)"
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == [PIKEPDF_VERSION, QPDF_VERSION]


def test_so_as_distribuicoes_de_terceiros_do_grupo_do_executor(image: str) -> None:
    code = (
        "import importlib.metadata as m\n"
        "print('\\n'.join(sorted(d.metadata['Name'].lower() for d in m.distributions())))"
    )

    result = run_python(image, code)

    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["lxml", "packaging", "pikepdf", "pillow"]


def test_instaladores_nao_ficam_na_imagem(image: str) -> None:
    result = run_python(image, "import shutil; print(shutil.which('uv'), shutil.which('pip'))")

    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["None", "None"]


@pytest.mark.skipif(
    sys.platform != "linux",
    # O compartilhamento de arquivos do Docker Desktop mostra o arquivo como sendo de quem o lê,
    # dentro ou fora do container; o dono real só é observável num host Linux (CI e VPS).
    reason="dono do arquivo só é observável com Docker num host Linux",
)
def test_executor_escreve_como_uid_61000(image: str, tmp_path: Path) -> None:
    work = make_work_dir(tmp_path, [("a.csv", VALID)])

    result = run_executor(image, work)

    assert result.returncode == SUCCEEDED, result.stderr
    assert (work / "out" / "report.json").stat().st_uid == EXECUTOR_UID


def test_imagem_roda_como_61000_por_padrao(image: str) -> None:
    result = run_python(image, "import os; print(os.getuid(), os.getgid())")

    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == [str(EXECUTOR_UID), str(EXECUTOR_GID)]


def test_entrypoint_e_fixo_no_executor_sobre_o_work(image: str) -> None:
    result = docker("image", "inspect", "--format", "{{json .Config.Entrypoint}}", image)

    assert json.loads(result.stdout) == ["python", "-m", "goblinshift.processing.executor", "/work"]


def test_bytecode_ja_vem_compilado_na_imagem(image: str) -> None:
    code = (
        "import importlib.util, os, goblinshift.processing.executor.execution as m\n"
        "print(os.path.exists(importlib.util.cache_from_source(m.__file__)))"
    )

    result = run_python(image, code)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "True"


def test_diretorio_corrente_nao_entra_no_sys_path(image: str) -> None:
    # Com `python -m`, o diretório corrente (/work) iria para sys.path[0]; um arquivo .py ali
    # sombrearia módulos da biblioteca padrão.
    result = run_python(image, "import sys; print('' in sys.path or '/work' in sys.path)")

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "False"


# --- Receitas da fatia 5 rodando de verdade na imagem (1.2, 1.4 e 3.9) -----------------------


def read_json(path: Path) -> dict[str, Any]:
    document: dict[str, Any] = json.loads(path.read_bytes())
    return document


def image_forge_batch() -> list[tuple[str, bytes]]:
    """Foto Display P3 deitada pela orientação 6, PNG de 16 bits e bomba de descompressão."""
    photo = media.jpeg(
        media.solid("RGB", (40, 20), (200, 30, 30)),
        exif=media.exif(orientation=6, with_gps=True),
        icc_profile=rgb_profile(DISPLAY_P3, "Display P3"),
    )
    return [
        ("foto.jpg", photo),
        ("profundo.png", media.png_16_bit(4, 4, color_type=2)),
        ("bomba.png", media.png_bomb(30_000, 30_000)),
    ]


def detox_batch() -> list[tuple[str, bytes]]:
    """Nomes em colisão, ZIP que é JAR, cópia exata e HTML disfarçado de texto."""
    return [
        ("Foto.png", samples.png(1)),
        ("foto.PNG", samples.png(2)),
        ("ferramenta.zip", samples.jar()),
        ("Foto (1).png", samples.png(1)),
        ("pagina.txt", b"<!DOCTYPE html><html><body>oi</body></html>\n"),
    ]


def run_recipe(
    image: str, root: Path, batch: list[tuple[str, bytes]], recipe_id: str, **parameters: object
) -> Path:
    work = make_work_dir(root, batch, recipe_id, parameters)
    result = run_executor(image, work)
    assert result.returncode == SUCCEEDED, result.stderr
    return work / "out"


def package_digest(out: Path, recipe_id: str) -> str:
    return hashlib.sha256((out / f"{recipe_id}.zip").read_bytes()).hexdigest()


def test_image_forge_converte_orienta_e_recusa_na_imagem(image: str, tmp_path: Path) -> None:
    out = run_recipe(image, tmp_path, image_forge_batch(), "image-forge", make_thumbnails=True)

    report = read_json(out / "report.json")
    statuses = [(item["status"], item.get("code")) for item in report["items"]]
    assert statuses == [
        ("ok", None),
        ("rejected", "UNSUPPORTED_BIT_DEPTH"),
        ("rejected", "IMAGE_TOO_LARGE"),
    ]
    assert report["items"][0]["details"]["color_profile"] == "display-p3-converted"
    with Image.open(out / "images" / "foto.jpg") as converted:
        assert converted.size == (20, 40)
        assert not converted.getexif()
        assert "icc_profile" not in converted.info
    with zipfile.ZipFile(out / "image-forge.zip") as archive:
        assert archive.namelist() == [
            "images/foto.jpg",
            "thumbnails/foto.jpg",
            "manifest.json",
            "report.json",
        ]


def test_image_forge_gera_o_mesmo_zip_em_duas_execucoes_na_imagem(
    image: str, tmp_path: Path
) -> None:
    first = run_recipe(image, tmp_path / "1", image_forge_batch(), "image-forge")
    second = run_recipe(image, tmp_path / "2", image_forge_batch(), "image-forge")

    assert package_digest(first, "image-forge") == package_digest(second, "image-forge")


def test_downloads_detox_separa_deduplica_e_recusa_na_imagem(image: str, tmp_path: Path) -> None:
    out = run_recipe(image, tmp_path, detox_batch(), "downloads-detox")

    manifest = read_json(out / "manifest.json")
    assert [(entry["status"], entry.get("duplicate_of")) for entry in manifest["inputs"]] == [
        ("ok", None),
        ("ok", None),
        ("ok", None),
        ("duplicate", 0),
        ("rejected", None),
    ]
    with zipfile.ZipFile(out / "downloads-detox.zip") as archive:
        assert archive.namelist() == [
            "images/Foto.png",
            "images/foto_1.PNG",
            "archives/ferramenta.zip",
            "manifest.json",
            "report.json",
        ]
        assert archive.read("archives/ferramenta.zip") == samples.jar()


def test_downloads_detox_gera_o_mesmo_zip_em_duas_execucoes_na_imagem(
    image: str, tmp_path: Path
) -> None:
    first = run_recipe(image, tmp_path / "1", detox_batch(), "downloads-detox")
    second = run_recipe(image, tmp_path / "2", detox_batch(), "downloads-detox")

    assert package_digest(first, "downloads-detox") == package_digest(second, "downloads-detox")


# --- Media Mill na imagem: o FFmpeg mínimo do ADR 036 -----------------------------------------


def run_tool(image: str, *argv: str) -> subprocess.CompletedProcess[str]:
    return docker(*fixed_profile("--entrypoint", argv[0], image, *argv[1:]))


def test_ffmpeg_e_ffprobe_tem_a_versao_fixada(image: str) -> None:
    for tool in ("ffmpeg", "ffprobe"):
        result = run_tool(image, tool, "-hide_banner", "-version")

        assert result.returncode == 0, result.stderr
        assert result.stdout.startswith(f"{tool} version {FFMPEG_VERSION} ")


def test_ffmpeg_so_le_mov_e_matroska(image: str) -> None:
    result = run_tool(image, "ffmpeg", "-hide_banner", "-demuxers")

    assert result.returncode == 0, result.stderr
    # Depois da legenda ("D. = Demuxing supported"), uma linha por formato: " D   <nomes> <desc>".
    listing = result.stdout.split("--", 1)[1]
    demuxers = [line.split()[1] for line in listing.splitlines() if line.startswith(" D")]
    assert demuxers == ["matroska,webm", "mov,mp4,m4a,3gp,3g2,mj2"]


def test_ffmpeg_so_tem_os_protocolos_file_e_pipe(image: str) -> None:
    result = run_tool(image, "ffmpeg", "-hide_banner", "-protocols")

    assert result.returncode == 0, result.stderr
    names = [line.strip() for line in result.stdout.splitlines()[1:]]
    assert names == ["Input:", "file", "pipe", "Output:", "file", "pipe"]


@pytest.mark.parametrize("demuxer", ["hls", "concat", "image2", "lavfi"])
def test_demuxers_que_abrem_outros_arquivos_nao_existem(image: str, demuxer: str) -> None:
    result = run_tool(image, "ffmpeg", "-nostdin", "-f", demuxer, "-i", "file:/nao-existe")

    assert result.returncode != 0
    assert f"Unknown input format: '{demuxer}'" in result.stderr


def media_batch(root: Path) -> list[tuple[str, bytes]]:
    """Celular em pé (rotação 90), WebM VP9/Opus, HLS disfarçado de .mp4 e 4K."""
    root.mkdir()
    mp4 = video.generate(video.CLIPS["mp4"], root / "a.mp4")
    return [
        ("celular.mp4", video.rotated(mp4, root / "r.mp4", 90).read_bytes()),
        ("clipe.webm", video.generate(video.CLIPS["webm_vp9_opus"], root / "b.webm").read_bytes()),
        ("lista.mp4", video.HLS_PLAYLIST),
        ("grande.mp4", video.generate(video.CLIPS["mp4_4k"], root / "c.mp4").read_bytes()),
    ]


def test_media_mill_converte_e_recusa_na_imagem(image: str, tmp_path: Path) -> None:
    batch = media_batch(tmp_path / "videos")

    out = run_recipe(image, tmp_path, batch, "media-mill", audio_format="flac", thumbnail_count=3)

    report = read_json(out / "report.json")
    statuses = [(item["status"], item.get("code")) for item in report["items"]]
    assert statuses == [
        ("ok", None),
        ("ok", None),
        ("rejected", "UNSUPPORTED_FORMAT"),
        ("rejected", "MEDIA_TOO_LARGE"),
    ]
    with Image.open(out / "thumbnails" / "celular-10pct.jpg") as thumbnail:
        assert thumbnail.size == (240, 320)
    with zipfile.ZipFile(out / "media-mill.zip") as archive:
        assert archive.namelist() == [
            "audio/celular.flac",
            "thumbnails/celular-10pct.jpg",
            "thumbnails/celular-50pct.jpg",
            "thumbnails/celular-90pct.jpg",
            "audio/clipe.flac",
            "thumbnails/clipe-10pct.jpg",
            "thumbnails/clipe-50pct.jpg",
            "thumbnails/clipe-90pct.jpg",
            "manifest.json",
            "report.json",
        ]
        assert archive.read("audio/clipe.flac").startswith(b"fLaC")


def test_media_mill_gera_o_mesmo_zip_em_duas_execucoes_na_imagem(
    image: str, tmp_path: Path
) -> None:
    batch = media_batch(tmp_path / "videos")

    first = run_recipe(image, tmp_path / "1", batch, "media-mill", thumbnail_count=3)
    second = run_recipe(image, tmp_path / "2", batch, "media-mill", thumbnail_count=3)

    assert package_digest(first, "media-mill") == package_digest(second, "media-mill")


# --- PDF Press na imagem (1.5; ADR 035) -------------------------------------------------------


def test_pdf_press_junta_os_pdfs_na_imagem(image: str, tmp_path: Path) -> None:
    batch = [("a.pdf", pdf_samples.document("a", pages=2)), ("b.pdf", pdf_samples.document("b"))]

    out = run_recipe(image, tmp_path, batch, "pdf-press")

    assert pdf_samples.markers((out / "merged.pdf").read_bytes()) == ["a:1", "a:2", "b:1"]
    with zipfile.ZipFile(out / "pdf-press.zip") as archive:
        assert archive.namelist() == ["page-map.json", "merged.pdf", "manifest.json", "report.json"]


def test_pdf_press_gera_o_mesmo_zip_em_duas_execucoes_na_imagem(image: str, tmp_path: Path) -> None:
    batch = [("a.pdf", pdf_samples.document("a", pages=3))]

    first = run_recipe(image, tmp_path / "1", batch, "pdf-press", operation="split")
    second = run_recipe(image, tmp_path / "2", batch, "pdf-press", operation="split")

    assert package_digest(first, "pdf-press") == package_digest(second, "pdf-press")


@pytest.mark.parametrize(
    ("data", "code"),
    [
        pytest.param(
            pdf_samples.document(mutate=pdf_samples.javascript_name_tree),
            "PDF_UNSUPPORTED_FEATURE",
            id="javascript",
        ),
        pytest.param(pdf_samples.truncated(pdf_samples.document()), "PDF_INVALID", id="truncado"),
        pytest.param(
            pdf_samples.page_tree_with_many_kids(2_000_000),
            "INPUT_LIMIT_EXCEEDED",
            id="bomba-de-paginas",
        ),
        pytest.param(pdf_samples.deep_page_tree(10_000), "PDF_INVALID", id="arvore-profunda"),
    ],
)
def test_pdf_hostil_falha_deterministicamente_dentro_dos_limites_do_executor(
    image: str, tmp_path: Path, data: bytes, code: str
) -> None:
    work = make_work_dir(tmp_path, [("hostil.pdf", data)], "pdf-press", {})

    result = run_executor(image, work, *RESOURCE_LIMITS)

    assert result.returncode == RECIPE_FAILURE, result.stderr
    assert read_json(work / "failure.json")["code"] == code
