"""Teste de aceitação do ambiente do executor (docs/GoblinShiftPlan.md, 3.8; ADRs 004 e 005).

Roda na própria VPS, como root, antes de abrir o serviço e a cada mudança no host (gVisor,
Docker, kernel, perfil do executor ou executor-ctl), com o worker parado:

    cd /srv/goblinshift/app/deploy && sudo python3 -B -m acceptance.run

O `-B` evita deixar `__pycache__` do root no checkout, que pertence ao usuário goblinshift; o
`-m` a partir de deploy/ torna importável o pacote do executor-ctl.

Prova, dentro do executor e com os limites de 1.7: container sob runsc com o perfil fixo; sem
rede; ENOSPC ao encher a área de 6 GiB; RLIMIT_FSIZE por arquivo; morte por falta de memória
antes de 2 GiB; fork bomb contido em 64 processos; que a aplicação não alcança o
/var/run/docker.sock; e que o executor-ctl de produção responde no socket com as permissões do
contrato. Sai com 0 só se todos os critérios passarem.

As sondas precisam de um comando e de uma montagem a mais (o probe.py), que o protocolo do
executor-ctl de propósito não aceita. Por isso o teste não passa pelo socket para rodá-las: monta o
`docker run` com as MESMAS opções do perfil de produção (`executor_ctl.profile.run_options`) e as
mesmas regras da área (`executor_ctl.workarea`), lidas da configuração instalada. O socket é
conferido à parte; as quatro operações são cobertas pelos testes de integração
(tests/executor_ctl), que a CI roda sob runsc.

Por serem idênticas a uma tentativa de produção (`<área>/<uuid>` e `gs-exec-<uuid>`) mas sem linha
no banco, as sondas seriam paradas e removidas pela manutenção no ciclo seguinte (3.4: tentativa
sem registro é sobra a limpar). Por isso o container `maintenance` do Compose fica parado durante
as sondas e volta no fim, mesmo se uma sonda quebrar; a volta é conferida e entra no veredito
(ADR 017).

Usa só a biblioteca padrão e roda no Python do host (3.12 no Ubuntu 24.04).
"""

import grp
import json
import os
import stat
import subprocess
import sys
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from executor_ctl import profile, workarea
from executor_ctl.client import call
from executor_ctl.config import DEFAULT_CONFIG_PATH, Config, load_config
from executor_ctl.docker_cli import DockerCli

GIB = 1024**3
MIB = 1024**2
# Tamanho do filesystem exec-work (deploy/provision.sh). O ext4 gasta uma parte com metadados,
# então a área útil fica um pouco abaixo; menos que o mínimo indica outro limite agindo.
WORK_AREA_BYTES = 6 * GIB
WORK_AREA_MIN_USABLE_BYTES = 5.5 * GIB
STOP_GRACE_S = 10
APP_PROJECT = "goblinshift"
APP_SERVICE = "api"
MAINTENANCE_SERVICE = "maintenance"
MAINTENANCE_CRITERION = "manutenção religada depois das sondas"
# Folga para a manutenção terminar o ciclo em curso ao receber SIGTERM: um ciclo pode esperar a
# parada de um executor (10 s) e consultas de lote com statement_timeout de 30 s.
MAINTENANCE_STOP_S = 60
SOCKET_TIMEOUT_S = 10
PROBE_IMAGE = (
    "python:3.14.9-slim-trixie"
    "@sha256:a145a961ec2cf5741772d5e39773d979271132884fd778f6c3fad34e52b85402"
)
PROBE_SCRIPT = Path(__file__).with_name("probe.py")
# Dono de <area>/<id> no contrato da fatia 4: o UID do worker, com o grupo gs-exec.
WORKER_UID = 10001
ATTEMPT_DIR_MODE = 0o2770
INPUTS_DIR_MODE = 0o2750


@dataclass(frozen=True)
class Outcome:
    criterion: str
    passed: bool
    evidence: str


@dataclass(frozen=True)
class ExecutorState:
    """Estado de um container de sonda, como o Docker o relata."""

    running: bool
    exit_code: int
    oom_killed: bool
    # Mensagem do runtime quando o container não terminou por conta própria (vazia se terminou).
    error: str
    host_config: dict[str, Any]


@dataclass(frozen=True)
class Host:
    """O que a configuração instalada do executor-ctl diz sobre este host."""

    config: Config
    group_id: int


@dataclass(frozen=True)
class ProbeRun:
    state: ExecutorState
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
    config = load_config(DEFAULT_CONFIG_PATH)
    host = Host(config, grp.getgrnam(config.group).gr_gid)
    subprocess.run(["docker", "pull", "--quiet", PROBE_IMAGE], check=True)

    outcomes = [
        check_control_socket(host),
        *run_with_maintenance_suspended(lambda: check_probes(host)),
        check_app_without_docker_socket(),
    ]
    for outcome in outcomes:
        verdict = "PASSOU" if outcome.passed else "FALHOU"
        print(f"{verdict}  {outcome.criterion}: {outcome.evidence}")
    return 0 if all(outcome.passed for outcome in outcomes) else 1


def check_probes(host: Host) -> list[Outcome]:
    return [
        *check_identity(run_probe(host, "identity", timeout_s=60)),
        check_network(run_probe(host, "network", timeout_s=60)),
        check_disk(run_probe(host, "disk", timeout_s=900)),
        check_file_size(run_probe(host, "file_size", timeout_s=300)),
        check_memory(run_probe(host, "memory", timeout_s=180)),
        check_pids(run_probe(host, "pids", timeout_s=120)),
    ]


def run_with_maintenance_suspended(
    probes: Callable[[], list[Outcome]], docker: Callable[..., str] | None = None
) -> list[Outcome]:
    """Roda as sondas com a manutenção parada; o último resultado diz se ela voltou ao ar.

    Manutenção que já estava parada fica como estava. Se uma sonda levantar exceção, a manutenção
    volta antes de a exceção seguir, e uma falha nessa volta é impressa no stderr.
    """
    docker = docker or _docker_output
    containers = _service_ids(docker, MAINTENANCE_SERVICE)
    if not containers:
        return [
            *probes(),
            Outcome(MAINTENANCE_CRITERION, True, "não estava rodando: deixada como estava"),
        ]
    print(
        f"manutenção parada durante as sondas; se este teste for interrompido à força, "
        f"religue com: {_restart_command(containers)}\n"
    )
    docker("stop", f"--time={MAINTENANCE_STOP_S}", *containers)
    try:
        outcomes = probes()
    except BaseException:
        restored = _restart_maintenance(docker, containers)
        if not restored.passed:
            print(f"FALHOU  {restored.criterion}: {restored.evidence}", file=sys.stderr)
        raise
    return [*outcomes, _restart_maintenance(docker, containers)]


def _restart_maintenance(docker: Callable[..., str], containers: list[str]) -> Outcome:
    recovery = f"religue com: {_restart_command(containers)}"
    try:
        docker("start", *containers)
        states = docker(
            "inspect", "--type=container", "--format={{.State.Running}}", *containers
        ).split()
    except subprocess.CalledProcessError as error:
        detail = (error.stderr or "").strip()[-200:]
        return Outcome(MAINTENANCE_CRITERION, False, f"docker {error.cmd[1]}: {detail}; {recovery}")
    if states != ["true"] * len(containers):
        return Outcome(MAINTENANCE_CRITERION, False, f"Running={states}; {recovery}")
    return Outcome(MAINTENANCE_CRITERION, True, f"container {' '.join(containers)} rodando")


def _service_ids(docker: Callable[..., str], service: str) -> list[str]:
    return docker(
        "ps",
        "--quiet",
        f"--filter=label=com.docker.compose.project={APP_PROJECT}",
        f"--filter=label=com.docker.compose.service={service}",
    ).split()


def _restart_command(containers: list[str]) -> str:
    return f"sudo docker start {' '.join(containers)}"


def check_control_socket(host: Host) -> Outcome:
    """O serviço systemd está no ar, com o socket root:gs-exec 0660, e responde ao status."""
    criterion = "executor-ctl responde no socket"
    path = host.config.socket
    try:
        info = path.lstat()
        response = call(path, "GET", f"/v1/attempts/{uuid.uuid4()}", timeout_s=SOCKET_TIMEOUT_S)
    except OSError as error:
        return Outcome(criterion, False, f"{path}: {type(error).__name__}: {error}")
    mode = stat.S_IMODE(info.st_mode)
    permissions_ok = (
        stat.S_ISSOCK(info.st_mode)
        and info.st_uid == 0
        and info.st_gid == host.group_id
        and mode == 0o660
    )
    answered = response.status == 200 and response.body.get("state") == "absent"
    return Outcome(
        criterion,
        permissions_ok and answered,
        f"dono {info.st_uid}:{info.st_gid} (gs-exec = {host.group_id}), modo {mode:o}; "
        f"status de tentativa inexistente: HTTP {response.status} {response.body}",
    )


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
        f"log {profile.LOG_DRIVER} com teto de {profile.LOG_OPTIONS['max-size']}, "
        f"UID {profile.EXECUTOR_UID}, sem docker.sock; "
        f"/ montado como {run.last.get('root_filesystem')!r}, "
        f"/tmp como {run.last.get('tmp_filesystem')!r}"  # noqa: S108 (texto, não arquivo)
    )
    fixed_profile = Outcome(
        "perfil fixo do executor",
        not mismatches and not run.timed_out,
        "; ".join(mismatches) or applied,
    )
    return [runtime, fixed_profile]


def _profile_mismatches(config: dict[str, Any], report: dict[str, Any]) -> list[str]:
    memory = profile.MEMORY_BYTES
    expected = {
        "NetworkMode": "none",
        "ReadonlyRootfs": True,
        "PidsLimit": profile.SANDBOX_TASKS_LIMIT,
        "Memory": memory,
        "MemorySwap": memory,
        "NanoCpus": profile.CPUS * 1_000_000_000,
    }
    mismatches = [
        f"{key}={config.get(key)!r}, esperado {value!r}"
        for key, value in expected.items()
        if config.get(key) != value
    ]
    ulimits = {u["Name"]: (u["Soft"], u["Hard"]) for u in config.get("Ulimits") or []}
    expected_ulimits = {
        "fsize": profile.FILE_SIZE_LIMIT_BYTES,
        "nproc": profile.PROCESS_LIMIT,
    }
    mismatches.extend(
        f"ulimit {name}={ulimits.get(name)!r}, esperado {limit}"
        for name, limit in expected_ulimits.items()
        if ulimits.get(name) != (limit, limit)
    )
    expected_log = {"Type": profile.LOG_DRIVER, "Config": profile.LOG_OPTIONS}
    if config.get("LogConfig") != expected_log:
        mismatches.append(f"LogConfig={config.get('LogConfig')!r}, esperado {expected_log!r}")
    if "ALL" not in (config.get("CapDrop") or []):
        mismatches.append(f"CapDrop={config.get('CapDrop')!r} sem ALL")
    if "no-new-privileges" not in (config.get("SecurityOpt") or []):
        mismatches.append(f"SecurityOpt={config.get('SecurityOpt')!r} sem no-new-privileges")
    if report.get("uid") != profile.EXECUTOR_UID:
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
        run.last.get("error") == "EFBIG" and written == profile.FILE_SIZE_LIMIT_BYTES,
        f"{run.last.get('error')} depois de {written / MIB:.0f} MiB num único arquivo",
    )


def check_memory(run: ProbeRun) -> Outcome:
    peak_mib = max((r.get("allocated_mib", 0) for r in run.reports), default=0)
    killed = run.state.oom_killed or run.state.exit_code == 137
    contained = (
        killed
        and not run.last.get("reached_ceiling", False)
        and peak_mib * MIB <= profile.MEMORY_BYTES
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
        run.last.get("error") == "EAGAIN" and forked < profile.PROCESS_LIMIT,
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


def run_probe(host: Host, probe: str, *, timeout_s: float) -> ProbeRun:
    attempt = uuid.uuid4()
    name = profile.container_name(attempt)
    docker = DockerCli()
    area = host.config.work_area
    create_attempt_dir(host, attempt)
    try:
        # As mesmas regras do start de produção: recusa área suja e prepara as permissões.
        prepared = workarea.prepare_for_start(area, attempt, host.group_id)
        options = profile.run_options(
            attempt, work_dir=prepared.path, runtime=host.config.runtime, executor_gid=host.group_id
        )
        started = time.monotonic()
        _docker_output(
            "run",
            "--detach",
            *options,
            "--env=PYTHONDONTWRITEBYTECODE=1",
            f"--mount=type=bind,src={PROBE_SCRIPT},dst=/opt/gs/probe.py,readonly",
            PROBE_IMAGE,
            "python",
            "/opt/gs/probe.py",
            probe,
        )
        timed_out = False
        try:
            subprocess.run(
                ["docker", "wait", name], check=True, capture_output=True, timeout=timeout_s
            )
        except subprocess.TimeoutExpired:
            timed_out = True
            docker.stop(name, STOP_GRACE_S)
        duration_s = time.monotonic() - started
        state = _inspect(name)
        output = _logs(name)
    finally:
        docker.remove(name)
        workarea.remove_attempt_dir(area, attempt)
    return ProbeRun(state, _json_lines(output), output[-500:], duration_s, timed_out)


def create_attempt_dir(host: Host, attempt: uuid.UUID) -> None:
    """Faz o papel do worker: <area>/<id> e inputs/, com os donos e modos do contrato."""
    work_dir = workarea.attempt_dir(host.config.work_area, attempt)
    inputs = work_dir / profile.INPUTS_DIR
    for path, mode in ((work_dir, ATTEMPT_DIR_MODE), (inputs, INPUTS_DIR_MODE)):
        path.mkdir()
        os.chown(path, WORKER_UID, host.group_id)
        path.chmod(mode)


def _inspect(name: str) -> ExecutorState:
    [info] = json.loads(_docker_output("inspect", "--type=container", name))
    state = info["State"]
    return ExecutorState(
        running=state["Running"],
        exit_code=state["ExitCode"],
        oom_killed=state["OOMKilled"],
        error=state.get("Error", ""),
        host_config=info["HostConfig"],
    )


def _logs(name: str) -> str:
    """Saída do container, com o stderr junto: um traceback da sonda também é evidência."""
    result = subprocess.run(
        ["docker", "logs", name],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    return result.stdout


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
