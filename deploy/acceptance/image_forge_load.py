"""Pior caso do Image Forge sob gVisor, antes do merge da fatia 5 (ADR 018; plano da fatia, item 9).

Roda na VPS, como root, a partir do checkout da branch, com o worker parado:

    cd /srv/goblinshift/app/deploy && sudo python3 -B -m acceptance.image_forge_load

O deploy só implanta a `main`, e a versão 1 do Image Forge não pode mudar depois de publicada
(1.6): por isso a medição não passa pela API. Constrói a imagem do executor do checkout, gera com
ela as 100 fotos de 24 MP do cenário `image-forge-carga` (scripts/e2e_fixtures.py; o Pillow é o da
imagem, o host não precisa de nada além do Docker) e roda uma tentativa de verdade: área de
trabalho `<área>/<uuid>`, as mesmas regras de posse do executor-ctl e as MESMAS opções do perfil de
produção (`executor_ctl.profile.run_options`: runsc, 1 vCPU, 2 GiB, sem rede), como as sondas do
teste de aceitação. A manutenção fica parada durante a tentativa pelo mesmo motivo (ADR 017).

Passa se a tentativa terminar com código 0, todas as imagens processadas, dentro dos 10 min do
teto (1.7). Imprime o tempo para o ADR 018. Usa só a biblioteca padrão (Python 3.12 do host).
"""

import grp
import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from acceptance.run import (
    STOP_GRACE_S,
    WORKER_UID,
    Host,
    Outcome,
    create_attempt_dir,
    run_with_maintenance_suspended,
)
from executor_ctl import profile, workarea
from executor_ctl.config import DEFAULT_CONFIG_PATH, load_config
from executor_ctl.docker_cli import DockerCli

REPO_ROOT = Path(__file__).resolve().parents[2]
IMAGE = "goblinshift-executor:medicao"
SCENARIO = "image-forge-carga"
ATTEMPT_LIMIT_S = 600.0  # 1.7: 10 min por tentativa
BUILD_TIMEOUT_S = 1800
GENERATE_TIMEOUT_S = 900
WORKER_FILE_MODE = 0o640
CRITERION = "100 imagens de 24 MP numa tentativa sob o perfil de produção (teto de 600 s)"


def main() -> int:
    if os.geteuid() != 0:
        print("rode com sudo", file=sys.stderr)
        return 2
    config = load_config(DEFAULT_CONFIG_PATH)
    host = Host(config, grp.getgrnam(config.group).gr_gid)
    print(f"construindo {IMAGE} a partir de {REPO_ROOT}")
    _docker("build", "--quiet", "--file", str(REPO_ROOT / "docker" / "executor.Dockerfile"),
            "--tag", IMAGE, str(REPO_ROOT), timeout=BUILD_TIMEOUT_S)  # fmt: skip
    with tempfile.TemporaryDirectory(prefix="gs-carga-") as generated:
        fixtures = Path(generated)
        print("gerando as fixtures na própria imagem")
        _generate(fixtures)
        expected = json.loads((fixtures / "expected.json").read_text(encoding="utf-8"))
        [outcome] = run_with_maintenance_suspended(lambda: [measure(host, fixtures, expected)])
    verdict = "PASSOU" if outcome.passed else "FALHOU"
    print(f"{verdict}  {outcome.criterion}: {outcome.evidence}")
    return 0 if outcome.passed else 1


def plan_job(
    expected: Mapping[str, Any],
) -> tuple[dict[str, Any], list[tuple[str, str]]]:
    """O job.json que o worker gravaria e as cópias (caminho no gerador → nome em inputs/)."""
    entries = []
    copies = []
    for entry in expected["inputs"]:
        manifest = entry["manifest"]
        name = f"{manifest['position']:04}.bin"
        entries.append(
            {
                "position": manifest["position"],
                "path": name,
                "original_name": manifest["original_name"],
                "size": manifest["size"],
                "sha256": manifest["sha256"],
            }
        )
        copies.append((entry["path"], name))
    job = {
        "recipe_id": expected["recipe_id"],
        "recipe_version": expected["recipe_version"],
        "parameters": expected["parameters"],
        "inputs": entries,
    }
    return job, copies


def judge(
    *, exit_code: int, duration_s: float, timed_out: bool, summary: Mapping[str, int] | None
) -> Outcome:
    all_ok = summary is not None and summary["ok"] == summary["inputs"]
    passed = exit_code == 0 and not timed_out and all_ok and duration_s <= ATTEMPT_LIMIT_S
    evidence = f"{duration_s:.0f} s, saída {exit_code}, resumo {dict(summary or {})}"
    if timed_out:
        evidence += "; parado pelo teto"
    return Outcome(CRITERION, passed, evidence)


def measure(host: Host, fixtures: Path, expected: Mapping[str, Any]) -> Outcome:
    attempt = uuid.uuid4()
    name = profile.container_name(attempt)
    area = host.config.work_area
    docker = DockerCli()
    create_attempt_dir(host, attempt)
    try:
        work_dir = workarea.attempt_dir(area, attempt)
        _fill_work_dir(work_dir, fixtures, expected, host.group_id)
        prepared = workarea.prepare_for_start(area, attempt, host.group_id)
        options = profile.run_options(
            attempt, work_dir=prepared.path, runtime=host.config.runtime, executor_gid=host.group_id
        )
        started = time.monotonic()
        _docker("run", "--detach", *options, IMAGE)
        timed_out = False
        try:
            subprocess.run(
                ["docker", "wait", name],
                check=True,
                capture_output=True,
                timeout=ATTEMPT_LIMIT_S + STOP_GRACE_S,
            )
        except subprocess.TimeoutExpired:
            timed_out = True
            docker.stop(name, STOP_GRACE_S)
        duration_s = time.monotonic() - started
        exit_code = int(_docker("inspect", "--format", "{{.State.ExitCode}}", name).strip())
        summary = _summary(work_dir / profile.OUTPUT_DIR / "report.json")
    finally:
        docker.remove(name)
        workarea.remove_attempt_dir(area, attempt)
    return judge(exit_code=exit_code, duration_s=duration_s, timed_out=timed_out, summary=summary)


def _generate(target: Path) -> None:
    """Roda o gerador dentro da imagem do executor, que já tem o Pillow do lock."""
    target.chmod(0o777)  # o gerador roda como o UID 61000 da imagem
    _docker(
        "run", "--rm", "--network=none", "--pull=never",
        "--env=PYTHONPATH=/repo:/opt/goblinshift",
        f"--mount=type=bind,src={REPO_ROOT},dst=/repo,readonly",
        f"--mount=type=bind,src={target},dst=/fixtures",
        "--workdir=/repo", "--entrypoint=python", IMAGE,
        "-m", "scripts.e2e_fixtures", SCENARIO, "/fixtures",
        timeout=GENERATE_TIMEOUT_S,
    )  # fmt: skip


def _fill_work_dir(
    work_dir: Path, fixtures: Path, expected: Mapping[str, Any], group_id: int
) -> None:
    """Faz o papel do worker: job.json e inputs/, com o dono e o modo do contrato."""
    job, copies = plan_job(expected)
    inputs = work_dir / profile.INPUTS_DIR
    for source, name in copies:
        _install(inputs / name, (fixtures / source).read_bytes(), group_id)
    _install(work_dir / "job.json", json.dumps(job).encode(), group_id)


def _install(path: Path, data: bytes, group_id: int) -> None:
    path.write_bytes(data)
    os.chown(path, WORKER_UID, group_id)
    path.chmod(WORKER_FILE_MODE)


def _summary(report: Path) -> dict[str, int] | None:
    try:
        document = json.loads(report.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    summary: dict[str, int] = document["summary"]
    return summary


def _docker(*args: str, timeout: float = 120) -> str:
    result = subprocess.run(
        ["docker", *args], check=False, capture_output=True, text=True, timeout=timeout
    )
    if result.returncode != 0:
        raise RuntimeError(f"docker {args[0]} falhou: {result.stderr.strip()}")
    return result.stdout


if __name__ == "__main__":
    sys.exit(main())
