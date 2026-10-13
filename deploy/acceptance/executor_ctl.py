"""`executor-ctl` mínimo da fatia 1: PROTÓTIPO DESCARTÁVEL (docs/GoblinShiftPlan.md, 3.6 e 3.9).

Existe só para o teste de aceitação provar que o perfil do executor funciona na VPS. A fatia 4 o
substitui pelo serviço systemd com socket Unix e as quatro operações, e este arquivo é apagado.

O que já segue o plano e precisa sobreviver à troca: o nome derivado do `attempt_id` validado como
UUID, a recusa de área de trabalho suja e o perfil fixo de `docker run` (`_run_profile`).
"""

import json
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

IMAGE = (
    "python:3.14.8-slim-trixie"
    "@sha256:a2b82f3c48559aa0a8446d9af49826b6e2b2016f4cd2afabfe6013ec53729170"
)
WORK_AREA = Path("/srv/goblinshift/exec-work")
PROBE_SCRIPT = Path(__file__).with_name("probe.py")

# Limites do executor (docs/GoblinShiftPlan.md, 1.7).
CPUS = 1
MEMORY_BYTES = 2 * 1024**3
# Processos e threads da receita: RLIMIT_NPROC, aplicado pelo próprio gVisor (o 65º fork recebe
# EAGAIN). O --pids-limit do Docker conta também as tarefas do gVisor no host (31 ociosas, mais
# cerca de 2 por processo da receita) e por isso é só a rede de segurança do sandbox (ADR 015).
PROCESS_LIMIT = 64
SANDBOX_TASKS_LIMIT = 256
# RLIMIT_FSIZE por arquivo: o dobro do maior arquivo de saída (ZIP de 512 MiB), para caber
# temporários; o teto da área inteira continua sendo o filesystem de 6 GiB.
FILE_SIZE_LIMIT_BYTES = 1024**3
# UID sem conta no host, para que nada do executor coincida com um usuário real da VPS.
EXECUTOR_UID = 61000

# O ext4 cria lost+found na raiz; ele não conta como resto de tentativa anterior.
_FILESYSTEM_ENTRIES = frozenset({"lost+found"})


class WorkAreaNotMountedError(RuntimeError):
    """Sem o filesystem de tamanho fixo, a tentativa escreveria no disco raiz da VPS (ADR 005)."""


class DirtyWorkAreaError(RuntimeError):
    """A área de trabalho ainda tem arquivos de outra tentativa (ADR 005)."""


@dataclass(frozen=True)
class ExecutorState:
    """Estado de um container de tentativa, como o Docker o relata."""

    running: bool
    exit_code: int
    oom_killed: bool
    # Mensagem do runtime quando o container não terminou por conta própria (vazia se terminou).
    error: str
    host_config: dict[str, Any]


def start(attempt_id: uuid.UUID, probe: str) -> None:
    """Cria e inicia o container da tentativa; a sonda faz o papel da receita no protótipo."""
    if not WORK_AREA.is_mount():
        raise WorkAreaNotMountedError(f"{WORK_AREA} não é um ponto de montagem")
    entries = {entry.name for entry in WORK_AREA.iterdir()} - _FILESYSTEM_ENTRIES
    if entries:
        raise DirtyWorkAreaError(f"{WORK_AREA} não está vazia: {sorted(entries)[:5]}")
    _docker("run", "--detach", *_run_profile(attempt_id), "python", "/opt/gs/probe.py", probe)


def wait(attempt_id: uuid.UUID, timeout_s: float) -> None:
    """Espera o container terminar; levanta `subprocess.TimeoutExpired` se passar do prazo."""
    subprocess.run(
        ["docker", "wait", _name(attempt_id)],
        check=True,
        capture_output=True,
        timeout=timeout_s,
    )


def status(attempt_id: uuid.UUID) -> ExecutorState:
    [info] = json.loads(_docker("inspect", _name(attempt_id)))
    state = info["State"]
    return ExecutorState(
        running=state["Running"],
        exit_code=state["ExitCode"],
        oom_killed=state["OOMKilled"],
        error=state.get("Error", ""),
        host_config=info["HostConfig"],
    )


def logs(attempt_id: uuid.UUID) -> str:
    """Saída do container, com o stderr junto: um traceback da sonda também é evidência."""
    result = subprocess.run(
        ["docker", "logs", _name(attempt_id)],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    return result.stdout


def stop(attempt_id: uuid.UUID, grace_s: int) -> None:
    """SIGTERM, espera a carência e mata (docs/GoblinShiftPlan.md, 3.6, "Parada")."""
    _docker("stop", "--time", str(grace_s), _name(attempt_id))


def remove(attempt_id: uuid.UUID) -> None:
    """Remove o container; já removido conta como sucesso."""
    subprocess.run(["docker", "rm", "--force", _name(attempt_id)], check=True, capture_output=True)


def _run_profile(attempt_id: uuid.UUID) -> list[str]:
    """Perfil fixo do executor (docs/GoblinShiftPlan.md, 3.6): quem chama não escolhe opções."""
    memory = str(MEMORY_BYTES)
    return [
        f"--name={_name(attempt_id)}",
        "--label=goblinshift.executor=prototype",
        "--runtime=runsc",
        "--network=none",
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        f"--user={EXECUTOR_UID}:{EXECUTOR_UID}",
        f"--pids-limit={SANDBOX_TASKS_LIMIT}",
        f"--ulimit=nproc={PROCESS_LIMIT}",
        f"--memory={memory}",
        f"--memory-swap={memory}",
        f"--cpus={CPUS}",
        f"--ulimit=fsize={FILE_SIZE_LIMIT_BYTES}",
        "--env=PYTHONDONTWRITEBYTECODE=1",
        f"--mount=type=bind,src={PROBE_SCRIPT},dst=/opt/gs/probe.py,readonly",
        f"--mount=type=bind,src={WORK_AREA},dst=/work",
        "--pull=never",
        IMAGE,
    ]


def _name(attempt_id: uuid.UUID) -> str:
    # O tipo UUID já é a validação: um nome nunca é montado a partir de texto livre.
    return f"gs-exec-{attempt_id}"


def _docker(*args: str) -> str:
    result = subprocess.run(["docker", *args], check=True, capture_output=True, text=True)
    return result.stdout
