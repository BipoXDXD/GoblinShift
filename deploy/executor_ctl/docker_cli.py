"""Chamadas ao CLI do Docker, sempre com lista de argumentos e prazo (sem shell).

Os erros do CLI chegam como texto no stderr; aqui viram exceções com nome. Qualquer coisa
inesperada é `DockerUnavailableError`: o worker trata como executor indisponível, nunca como
"o container não existe".
"""

import json
import re
import subprocess
from dataclasses import dataclass
from typing import Literal

State = Literal["absent", "created", "running", "exited"]

# Prazo das chamadas curtas (inspect, run, rm); stop soma a carência a este valor.
COMMAND_TIMEOUT_S = 30.0
_MAX_DETAIL_CHARS = 300
# O Docker escreve "0001-01-01T00:00:00Z" para um horário que ainda não aconteceu.
_UNSET_TIME_PREFIX = "0001-01-01"
_RFC3339 = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,9})?(Z|[+-]\d{2}:\d{2})")
_DOCKER_STATES: dict[str, State] = {
    "created": "created",
    "running": "running",
    "paused": "running",
    "restarting": "running",
    "removing": "exited",
    "exited": "exited",
    "dead": "exited",
}


class DockerUnavailableError(RuntimeError):
    """O CLI não respondeu, o daemon está fora ou a resposta não tem o formato esperado."""


class ContainerNameConflictError(RuntimeError):
    """Já existe um container com o nome: outro start da mesma tentativa chegou antes."""


class ContainerRunningError(RuntimeError):
    """O Docker recusou remover porque o container ainda roda."""


@dataclass(frozen=True)
class ContainerStatus:
    state: State
    exit_code: int | None
    oom_killed: bool
    started_at: str | None
    finished_at: str | None

    @classmethod
    def absent(cls) -> "ContainerStatus":
        return cls(
            state="absent", exit_code=None, oom_killed=False, started_at=None, finished_at=None
        )

    def to_json(self) -> dict[str, object]:
        return {
            "state": self.state,
            "exit_code": self.exit_code,
            "oom_killed": self.oom_killed,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


class DockerCli:
    def __init__(self, binary: str = "docker", timeout_s: float = COMMAND_TIMEOUT_S) -> None:
        self._binary = binary
        self._timeout_s = timeout_s

    def inspect(self, name: str) -> ContainerStatus:
        result = self._execute(["inspect", "--type=container", name])
        if result.returncode != 0:
            if _is_missing(result.stderr):
                return ContainerStatus.absent()
            raise _unexpected("inspect", result)
        return parse_inspect(result.stdout)

    def run(self, arguments: list[str]) -> None:
        result = self._execute(arguments)
        if result.returncode == 0:
            return
        if "is already in use" in result.stderr:
            raise ContainerNameConflictError(_detail(result.stderr))
        raise _unexpected("run", result)

    def stop(self, name: str, grace_s: int) -> None:
        """SIGTERM, espera a carência e manda SIGKILL; ausente ou já terminado é sucesso."""
        result = self._execute(
            ["stop", f"--time={grace_s}", name], timeout_s=self._timeout_s + grace_s
        )
        if result.returncode != 0 and not _is_missing(result.stderr):
            raise _unexpected("stop", result)

    def remove(self, name: str) -> None:
        """Remove o container parado; ausente é sucesso, rodando é recusado."""
        result = self._execute(["rm", name])
        if result.returncode == 0 or _is_missing(result.stderr):
            return
        if "is running" in result.stderr:
            raise ContainerRunningError(_detail(result.stderr))
        raise _unexpected("rm", result)

    def _execute(
        self, arguments: list[str], *, timeout_s: float | None = None
    ) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                [self._binary, *arguments],
                capture_output=True,
                text=True,
                timeout=timeout_s or self._timeout_s,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            raise DockerUnavailableError(f"docker {arguments[0]} passou do prazo") from error
        except OSError as error:
            raise DockerUnavailableError(f"docker não executou: {type(error).__name__}") from error


def parse_inspect(output: str) -> ContainerStatus:
    """Lê o `State` do `docker inspect`; formato inesperado é erro, não "ausente"."""
    try:
        [info] = json.loads(output)
        state = info["State"]
        docker_status = state["Status"]
        exit_code = state["ExitCode"]
        oom_killed = state["OOMKilled"]
        started_at = _optional_time(state["StartedAt"])
        finished_at = _optional_time(state["FinishedAt"])
    except (ValueError, TypeError, KeyError) as error:
        raise DockerUnavailableError("docker inspect devolveu um formato inesperado") from error
    if docker_status not in _DOCKER_STATES:
        raise DockerUnavailableError(f"estado desconhecido do Docker: {docker_status!r}")
    if type(exit_code) is not int or type(oom_killed) is not bool:
        raise DockerUnavailableError("docker inspect com ExitCode ou OOMKilled de tipo errado")
    mapped = _DOCKER_STATES[docker_status]
    return ContainerStatus(
        state=mapped,
        exit_code=exit_code if mapped == "exited" else None,
        oom_killed=oom_killed,
        started_at=started_at,
        finished_at=finished_at,
    )


def _optional_time(value: object) -> str | None:
    if not isinstance(value, str):
        raise TypeError("horário do Docker não é texto")
    if value.startswith(_UNSET_TIME_PREFIX):
        return None
    if not _RFC3339.fullmatch(value):
        raise ValueError("horário do Docker fora do RFC 3339")
    return value


def _is_missing(stderr: str) -> bool:
    return "no such" in stderr.lower()


def _detail(stderr: str) -> str:
    return stderr.strip()[-_MAX_DETAIL_CHARS:]


def _unexpected(command: str, result: subprocess.CompletedProcess[str]) -> DockerUnavailableError:
    return DockerUnavailableError(
        f"docker {command} saiu com {result.returncode}: {_detail(result.stderr)}"
    )
