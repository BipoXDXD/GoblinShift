"""Teste de aceitação do ambiente do executor (docs/GoblinShiftPlan.md, 3.8; ADRs 004 e 005).

Roda na própria VPS, como root, antes de abrir o serviço e a cada mudança no host (gVisor,
Docker, kernel ou perfil do executor):

    sudo python3 deploy/acceptance/run.py

Prova, dentro do executor e com os limites de 1.7: container sob runsc com o perfil fixo; sem
rede; ENOSPC ao encher a área de 6 GiB; RLIMIT_FSIZE por arquivo; morte por falta de memória
antes de 2 GiB; fork bomb contido em 64 processos; e que a aplicação não alcança o
/var/run/docker.sock. Sai com 0 só se todos os critérios passarem.

Usa só a biblioteca padrão e roda no Python do host (3.12 no Ubuntu 24.04).
"""

import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from typing import Any

import executor_ctl

GIB = 1024**3
MIB = 1024**2
# Tamanho do filesystem exec-work (deploy/provision.sh). O ext4 gasta uma parte com metadados,
# então a área útil fica um pouco abaixo; menos que o mínimo indica outro limite agindo.
WORK_AREA_BYTES = 6 * GIB
WORK_AREA_MIN_USABLE_BYTES = 5.5 * GIB
STOP_GRACE_S = 10
APP_PROJECT = "goblinshift"
APP_SERVICE = "api"


@dataclass(frozen=True)
class Outcome:
    criterion: str
    passed: bool
    evidence: str


@dataclass(frozen=True)
class ProbeRun:
    state: executor_ctl.ExecutorState
    reports: list[dict[str, Any]]
    output_tail: str
    duration_s: float
    timed_out: bool

    @property
    def last(self) -> dict[str, Any]:
        return self.reports[-1] if self.reports else {}


def main() -> int:
    if os.geteuid() != 0:
        print("rode com sudo", file=sys.stderr)
        return 2
    print(f"runsc: {_first_line('runsc', '--version')}")
    print(f"Docker: {_first_line('docker', 'version', '--format', '{{.Server.Version}}')}")
    print(f"kernel do host: {os.uname().release}\n")
    subprocess.run(["docker", "pull", "--quiet", executor_ctl.IMAGE], check=True)

    outcomes = [
        *check_identity(run_probe("identity", timeout_s=60)),
        check_network(run_probe("network", timeout_s=60)),
        check_disk(run_probe("disk", timeout_s=900)),
        check_file_size(run_probe("file_size", timeout_s=300)),
        check_memory(run_probe("memory", timeout_s=180)),
        check_pids(run_probe("pids", timeout_s=120)),
        check_app_without_docker_socket(),
    ]
    for outcome in outcomes:
        verdict = "PASSOU" if outcome.passed else "FALHOU"
        print(f"{verdict}  {outcome.criterion}: {outcome.evidence}")
    return 0 if all(outcome.passed for outcome in outcomes) else 1


def check_identity(run: ProbeRun) -> list[Outcome]:
    config = run.state.host_config
    sandbox_kernel = run.last.get("kernel")
    host_kernel = os.uname().release
    runtime_ok = config.get("Runtime") == "runsc" and sandbox_kernel not in (None, host_kernel)
    runtime = Outcome(
        "container sob runsc",
        runtime_ok and not run.timed_out,
        f"runtime {config.get('Runtime')}; kernel visto no sandbox {sandbox_kernel}, "
        f"no host {host_kernel}",
    )
    mismatches = _profile_mismatches(config, run.last)
    applied = (
        "rede none, raiz read-only, sem capabilities, no-new-privileges, "
        f"UID {executor_ctl.EXECUTOR_UID}, sem docker.sock; "
        f"/ montado como {run.last.get('root_filesystem')!r}, "
        f"/tmp como {run.last.get('tmp_filesystem')!r}"  # noqa: S108 (texto, não arquivo)
    )
    profile = Outcome(
        "perfil fixo do executor",
        not mismatches and not run.timed_out,
        "; ".join(mismatches) or applied,
    )
    return [runtime, profile]


def _profile_mismatches(config: dict[str, Any], report: dict[str, Any]) -> list[str]:
    memory = executor_ctl.MEMORY_BYTES
    expected = {
        "NetworkMode": "none",
        "ReadonlyRootfs": True,
        "PidsLimit": executor_ctl.SANDBOX_TASKS_LIMIT,
        "Memory": memory,
        "MemorySwap": memory,
        "NanoCpus": executor_ctl.CPUS * 1_000_000_000,
    }
    mismatches = [
        f"{key}={config.get(key)!r}, esperado {value!r}"
        for key, value in expected.items()
        if config.get(key) != value
    ]
    ulimits = {u["Name"]: (u["Soft"], u["Hard"]) for u in config.get("Ulimits") or []}
    expected_ulimits = {
        "fsize": executor_ctl.FILE_SIZE_LIMIT_BYTES,
        "nproc": executor_ctl.PROCESS_LIMIT,
    }
    mismatches.extend(
        f"ulimit {name}={ulimits.get(name)!r}, esperado {limit}"
        for name, limit in expected_ulimits.items()
        if ulimits.get(name) != (limit, limit)
    )
    if "ALL" not in (config.get("CapDrop") or []):
        mismatches.append(f"CapDrop={config.get('CapDrop')!r} sem ALL")
    if "no-new-privileges" not in (config.get("SecurityOpt") or []):
        mismatches.append(f"SecurityOpt={config.get('SecurityOpt')!r} sem no-new-privileges")
    if report.get("uid") != executor_ctl.EXECUTOR_UID:
        mismatches.append(f"UID {report.get('uid')}")
    if report.get("root_write_error") != "EROFS":
        mismatches.append(f"escrita na raiz terminou em {report.get('root_write_error')}")
    if report.get("docker_sock_exists") is not False:
        mismatches.append("/var/run/docker.sock visível no executor")
    return mismatches


def check_network(run: ProbeRun) -> Outcome:
    report = run.last
    isolated = (
        report.get("interfaces") == ["lo"]
        and report.get("outbound_error") is not None
        and report.get("dns_error") is not None
    )
    return _judge(
        run,
        "sem rede",
        isolated,
        f"interfaces {report.get('interfaces')}; conexão externa: "
        f"{report.get('outbound_error')}; DNS: {report.get('dns_error')}",
    )


def check_disk(run: ProbeRun) -> Outcome:
    written = run.last.get("written_bytes", 0)
    stopped_by_filesystem = (
        run.last.get("error") == "ENOSPC"
        and WORK_AREA_MIN_USABLE_BYTES <= written <= WORK_AREA_BYTES
    )
    return _judge(
        run,
        "ENOSPC aos 6 GiB",
        stopped_by_filesystem,
        f"{run.last.get('error')} depois de {written / GIB:.2f} GiB em {run.duration_s:.0f} s",
    )


def check_file_size(run: ProbeRun) -> Outcome:
    written = run.last.get("written_bytes", 0)
    return _judge(
        run,
        "RLIMIT_FSIZE por arquivo",
        run.last.get("error") == "EFBIG" and written == executor_ctl.FILE_SIZE_LIMIT_BYTES,
        f"{run.last.get('error')} depois de {written / MIB:.0f} MiB num único arquivo",
    )


def check_memory(run: ProbeRun) -> Outcome:
    peak_mib = max((r.get("allocated_mib", 0) for r in run.reports), default=0)
    killed = run.state.oom_killed or run.state.exit_code == 137
    contained = (
        killed
        and not run.last.get("reached_ceiling", False)
        and peak_mib * MIB <= executor_ctl.MEMORY_BYTES
    )
    return _judge(
        run,
        "morte por falta de memória antes de 2 GiB",
        contained,
        f"saída {run.state.exit_code}, OOMKilled={run.state.oom_killed}, "
        f"último bloco confirmado em {peak_mib} MiB",
    )


def check_pids(run: ProbeRun) -> Outcome:
    forked = max((r.get("forked", 0) for r in run.reports), default=0)
    return _judge(
        run,
        "fork bomb contido em 64 processos",
        run.last.get("error") == "EAGAIN" and forked < executor_ctl.PROCESS_LIMIT,
        f"{forked} processos criados, depois {run.last.get('error')}; "
        f"saída {run.state.exit_code}, erro do runtime {run.state.error!r}",
    )


def check_app_without_docker_socket() -> Outcome:
    """O worker só existe na fatia 3; até lá vale a API, que usa a mesma imagem e o mesmo perfil."""
    criterion = "aplicação sem acesso ao docker.sock"
    container_id = _docker_output(
        "ps",
        "--quiet",
        f"--filter=label=com.docker.compose.project={APP_PROJECT}",
        f"--filter=label=com.docker.compose.service={APP_SERVICE}",
    ).strip()
    if not container_id:
        return Outcome(criterion, False, f"serviço {APP_SERVICE} do {APP_PROJECT} não está rodando")
    attempt = subprocess.run(
        [
            "docker",
            "exec",
            container_id,
            "python",
            "-c",
            "import os; os.open('/var/run/docker.sock', os.O_RDWR)",
        ],
        capture_output=True,
        text=True,
    )
    blocked = attempt.returncode != 0 and "FileNotFoundError" in attempt.stderr
    evidence = "FileNotFoundError ao abrir o socket" if blocked else attempt.stderr.strip()[-200:]
    return Outcome(criterion, blocked, evidence or "o socket abriu")


def run_probe(probe: str, *, timeout_s: float) -> ProbeRun:
    attempt = uuid.uuid4()
    _prepare_work_area()
    started = time.monotonic()
    executor_ctl.start(attempt, probe)
    try:
        timed_out = False
        try:
            executor_ctl.wait(attempt, timeout_s)
        except subprocess.TimeoutExpired:
            timed_out = True
            executor_ctl.stop(attempt, STOP_GRACE_S)
        duration_s = time.monotonic() - started
        state = executor_ctl.status(attempt)
        output = executor_ctl.logs(attempt)
    finally:
        executor_ctl.remove(attempt)
        _clear_work_area()
    return ProbeRun(state, _json_lines(output), output[-500:], duration_s, timed_out)


def _judge(run: ProbeRun, criterion: str, passed: bool, evidence: str) -> Outcome:
    if run.timed_out:
        return Outcome(criterion, False, f"passou do prazo; {evidence}")
    if not run.reports:
        return Outcome(
            criterion,
            False,
            f"a sonda não relatou nada (saída {run.state.exit_code}, "
            f"OOMKilled={run.state.oom_killed}, erro do runtime {run.state.error!r}): "
            f"{run.output_tail!r}",
        )
    return Outcome(criterion, passed, evidence)


def _prepare_work_area() -> None:
    # A raiz do filesystem pertence ao UID do executor, e só a ele.
    os.chown(executor_ctl.WORK_AREA, executor_ctl.EXECUTOR_UID, executor_ctl.EXECUTOR_UID)
    executor_ctl.WORK_AREA.chmod(0o700)


def _clear_work_area() -> None:
    for entry in executor_ctl.WORK_AREA.iterdir():
        if entry.name == "lost+found":
            continue
        if entry.is_dir() and not entry.is_symlink():
            shutil.rmtree(entry)
        else:
            entry.unlink()


def _json_lines(output: str) -> list[dict[str, Any]]:
    reports = []
    for line in output.splitlines():
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            reports.append(parsed)
    return reports


def _first_line(*command: str) -> str:
    return subprocess.run(command, check=True, capture_output=True, text=True).stdout.split("\n")[0]


def _docker_output(*args: str) -> str:
    return subprocess.run(["docker", *args], check=True, capture_output=True, text=True).stdout


if __name__ == "__main__":
    sys.exit(main())
