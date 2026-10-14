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
from collections.abc import Sequence
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = ROOT / "docker" / "executor.Dockerfile"
IMAGE = "goblinshift-executor:test"
RUNTIME = os.environ.get("GS_TEST_RUNTIME", "runc")
EXECUTOR_UID = 61000
EXECUTOR_GID = 61001
BUILD_TIMEOUT_SECONDS = 600
RUN_TIMEOUT_SECONDS = 120
# O usuário do container não é o dono do diretório temporário do teste; na VPS o acesso vem do
# grupo gs-exec (modo 2770), que o teste não consegue criar sem root.
WORLD_WRITABLE_DIR = 0o777
WORLD_READABLE_DIR = 0o755
WORLD_READABLE_FILE = 0o644
SUCCEEDED = 0
PILLOW_VERSION = "12.3.0"  # ADR 018; tests/test_dependencies.py confere o mesmo pin no lock

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


def run_executor(image: str, work: Path) -> subprocess.CompletedProcess[str]:
    mounts = [
        f"--user={EXECUTOR_UID}:{EXECUTOR_GID}",
        "--volume", f"{work}:/work",
        "--volume", f"{work / 'inputs'}:/work/inputs:ro",
    ]  # fmt: skip
    return docker(*fixed_profile(*mounts, image))


def make_work_dir(root: Path, tables: Sequence[tuple[str, bytes]]) -> Path:
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
    job = {"recipe_id": "csv-janitor", "recipe_version": 1, "parameters": {}, "inputs": entries}
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


def test_pillow_e_a_unica_distribuicao_de_terceiros(image: str) -> None:
    code = (
        "import importlib.metadata as m\n"
        "print('\\n'.join(sorted(d.metadata['Name'].lower() for d in m.distributions())))"
    )

    result = run_python(image, code)

    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["pillow"]


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
