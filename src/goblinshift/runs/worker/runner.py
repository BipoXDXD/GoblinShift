"""Runner: quem liga e desliga o executor de uma tentativa (docs/GoblinShiftPlan.md, 3.2 e 3.6).

A interface é a do `executor-ctl` da fatia 4: `start` idempotente pelo nome determinístico,
`status`, `stop` com carência e `remove`. Nada nela diz como o executor roda; o worker só prepara
a área de trabalho (`work_dir`) e coleta o que o executor deixou em `out/`.

Duas implementações: `ExecutorCtlRunner` (produção, container gVisor por tentativa criado pelo
serviço do host) e `LocalRunner`, que roda o executor em subprocesso, sem sandbox, e só existe
fora de produção (o processo recusa subir com ele em produção; 3.9, fatia 4).

Como o executor sobrevive ao worker
-----------------------------------
O worker não é pai do executor. `start` lança `local_supervisor`, que vira líder de um grupo de
processos novo, grava o ID do grupo, se desdobra e sai: o supervisor neto fica órfão, adotado
pelo init, e é ele quem espera o executor, aplica o teto de duração e grava `exit.json`. Assim:

- o worker morto por SIGKILL deixa o executor terminar, e a manutenção (outro processo) o
  encontra pelo nome, como faria com um container;
- o worker pausado por SIGSTOP não segura zumbis: quem colhe os processos é o init;
- `stop` sinaliza o grupo inteiro, executor e descendentes.

Limitação aceita no Runner de desenvolvimento: a vida do grupo é conferida com `killpg(…, 0)`, e
um ID de grupo reciclado pelo sistema depois de muito tempo seria confundido com o executor.
Num container sem init (PID 1 que não colhe órfãos), o Compose precisa de `init: true`.
"""

import contextlib
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from goblinshift.runs.store import container_name

# Teto da carência de `stop` (3.5: "concede até 10 segundos e força o encerramento").
MAX_STOP_GRACE = timedelta(seconds=10)
# Layout da área de trabalho (contrato da fatia 4): o executor (outro usuário, mesmo grupo) lê
# `job.json` e `inputs/` e escreve em `/work` e `out/`; o setgid faz tudo herdar o grupo.
WORK_DIR_MODE = 0o2770
INPUTS_DIR_MODE = 0o2750
OUT_DIR_MODE = 0o2770
INPUTS_DIR = "inputs"
OUT_DIR = "out"
# Depois do SIGKILL, quanto esperar o sistema confirmar que o grupo sumiu.
KILL_CONFIRM_TIMEOUT_S = 5.0
# O lançador só grava o grupo e se desdobra; demorar mais que isto é defeito.
LAUNCH_TIMEOUT_S = 10.0
_POLL_S = 0.05
_UUID_CHARS = len(str(uuid.UUID(int=0)))

EXECUTOR_MODULE = "goblinshift.processing.executor"
SUPERVISOR_MODULE = "goblinshift.runs.worker.local_supervisor"
STARTED_MARKER = "started"
PGID_FILE = "pgid"
EXIT_FILE = "exit.json"
LOG_FILE = "executor.log"


class ExecutorPhase(StrEnum):
    # Nunca começou: nenhum processo, nenhum container.
    ABSENT = "absent"
    RUNNING = "running"
    EXITED = "exited"


@dataclass(frozen=True)
class ExecutorStatus:
    phase: ExecutorPhase
    # Código de saída do executor; `None` se ainda roda ou se morreu sem relatá-lo (sinal).
    exit_code: int | None = None
    # O executor foi encerrado pelo teto de duração da tentativa.
    timed_out: bool = False
    # O kernel matou o executor por falta de memória (limite do container).
    oom_killed: bool = False

    @property
    def is_running(self) -> bool:
        return self.phase is ExecutorPhase.RUNNING

    @property
    def ran(self) -> bool:
        """O executor chegou a existir (`LeaseExpired.executor_ran`)."""
        return self.phase is not ExecutorPhase.ABSENT


class RunnerError(Exception):
    """O Runner não conseguiu cumprir a operação (por exemplo, remover um executor vivo)."""


class Runner(Protocol):
    def work_dir(self, attempt_id: uuid.UUID) -> Path:
        """Área de trabalho da tentativa, preparada pelo worker antes do `start`."""
        ...

    def create_work_dir(self, attempt_id: uuid.UUID) -> Path:
        """Cria a área vazia da tentativa (`inputs/` e `out/`) com as permissões do executor."""
        ...

    def list_work(self) -> list[uuid.UUID]:
        """Tentativas que ainda têm área de trabalho (para a manutenção limpar as encerradas)."""
        ...

    def start(self, attempt_id: uuid.UUID, image: str) -> ExecutorStatus:
        """Inicia o executor com a imagem fixada na Run; repetir adota o existente."""
        ...

    def status(self, attempt_id: uuid.UUID) -> ExecutorStatus: ...

    def stop(self, attempt_id: uuid.UUID, grace: timedelta) -> ExecutorStatus:
        """SIGTERM, até `grace` (no máximo 10 s) de espera e SIGKILL; devolve o estado final."""
        ...

    def remove(self, attempt_id: uuid.UUID) -> None:
        """Apaga a área de trabalho; só depois de a coleta ou o descarte estarem no banco."""
        ...


class LocalRunner:
    def __init__(
        self,
        work_root: Path,
        *,
        attempt_timeout: timedelta,
        command: Sequence[str] = (sys.executable, "-m", EXECUTOR_MODULE),
    ) -> None:
        self._root = work_root.resolve()
        self._timeout = attempt_timeout
        self._command = tuple(command)

    def work_dir(self, attempt_id: uuid.UUID) -> Path:
        return self._root / container_name(attempt_id)

    def create_work_dir(self, attempt_id: uuid.UUID) -> Path:
        self._root.mkdir(parents=True, exist_ok=True)
        work_dir = self.work_dir(attempt_id)
        create_work_layout(work_dir, group=None)
        return work_dir

    def list_work(self) -> list[uuid.UUID]:
        if not self._root.is_dir():
            return []
        found = {
            attempt_id
            for entry in self._root.iterdir()
            if (attempt_id := parse_attempt_id(entry.name[-_UUID_CHARS:])) is not None
            and self.work_dir(attempt_id) == entry
        }
        return sorted(found)

    def start(self, attempt_id: uuid.UUID, image: str) -> ExecutorStatus:
        """Roda o código do próprio repositório: `image` só existe para o contrato do Runner."""
        del image
        meta = self._meta_dir(attempt_id)
        meta.mkdir(parents=True, exist_ok=True)
        try:
            # O marcador exclusivo é o "nome determinístico": só o primeiro `start` lança.
            os.close(os.open(meta / STARTED_MARKER, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
        except FileExistsError:
            return self.status(attempt_id)
        with (meta / LOG_FILE).open("ab") as log:
            launcher = subprocess.Popen(  # noqa: S603 - argumentos fixos, sem shell
                [
                    sys.executable,
                    "-m",
                    SUPERVISOR_MODULE,
                    str(meta),
                    str(self._timeout.total_seconds()),
                    str(MAX_STOP_GRACE.total_seconds()),
                    *self._command,
                    str(self.work_dir(attempt_id)),
                ],
                start_new_session=True,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
            )
        exit_code = launcher.wait(timeout=LAUNCH_TIMEOUT_S)
        if exit_code != 0:
            raise RunnerError(f"lançador do executor saiu com {exit_code}")
        return self.status(attempt_id)

    def status(self, attempt_id: uuid.UUID) -> ExecutorStatus:
        meta = self._meta_dir(attempt_id)
        pgid = _read_pgid(meta)
        if pgid is None:
            return ExecutorStatus(ExecutorPhase.ABSENT)
        if _group_alive(pgid):
            return ExecutorStatus(ExecutorPhase.RUNNING)
        return _exited_status(meta)

    def stop(self, attempt_id: uuid.UUID, grace: timedelta) -> ExecutorStatus:
        pgid = _read_pgid(self._meta_dir(attempt_id))
        if pgid is None or not _group_alive(pgid):
            return self.status(attempt_id)
        _signal_group(pgid, signal.SIGTERM)
        if not _wait_group_exit(pgid, min(grace, MAX_STOP_GRACE).total_seconds()):
            _signal_group(pgid, signal.SIGKILL)
            _wait_group_exit(pgid, KILL_CONFIRM_TIMEOUT_S)
        return self.status(attempt_id)

    def remove(self, attempt_id: uuid.UUID) -> None:
        if self.status(attempt_id).is_running:
            raise RunnerError(f"executor de {attempt_id} ainda vivo; pare antes de remover")
        shutil.rmtree(self.work_dir(attempt_id), ignore_errors=True)
        shutil.rmtree(self._meta_dir(attempt_id), ignore_errors=True)

    def _meta_dir(self, attempt_id: uuid.UUID) -> Path:
        # Fora da área do executor: ele não lê nem adultera o que o supervisor registra.
        return self._root / f"{container_name(attempt_id)}.runner"


def create_work_layout(work_dir: Path, *, group: int | None) -> None:
    """Recria `work_dir` vazio com `inputs/` e `out/`; com `group`, o diretório passa a ele."""
    shutil.rmtree(work_dir, ignore_errors=True)
    work_dir.mkdir(mode=0o700)
    if group is not None:
        os.chown(work_dir, -1, group)
    work_dir.chmod(WORK_DIR_MODE)
    for name, mode in ((INPUTS_DIR, INPUTS_DIR_MODE), (OUT_DIR, OUT_DIR_MODE)):
        child = work_dir / name
        child.mkdir(mode=0o700)
        child.chmod(mode)


def parse_attempt_id(name: str) -> uuid.UUID | None:
    """O nome de uma área de tentativa é o UUID canônico; o resto (`lost+found`) não é."""
    try:
        parsed = uuid.UUID(name)
    except ValueError:
        return None
    return parsed if str(parsed) == name else None


def _read_pgid(meta: Path) -> int | None:
    try:
        return int((meta / PGID_FILE).read_text(encoding="ascii"))
    except FileNotFoundError:
        return None


def _group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # O grupo existe, mas é de outro usuário ou (no macOS) só tem zumbis ainda não colhidos
        # pelo init: para o Runner, ainda está vivo até sumir.
        return True
    return True


def _signal_group(pgid: int, signum: signal.Signals) -> None:
    # EPERM não é falha da parada: no macOS, o grupo que acabou de morrer responde assim até o
    # init colher os zumbis, e um grupo de outro usuário não é nosso para sinalizar. Quem decide
    # o desfecho é a espera seguinte pelo fim do grupo.
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(pgid, signum)


def _wait_group_exit(pgid: int, timeout_s: float) -> bool:
    deadline = time.monotonic() + timeout_s
    while _group_alive(pgid):
        if time.monotonic() >= deadline:
            return False
        time.sleep(_POLL_S)
    return True


def _exited_status(meta: Path) -> ExecutorStatus:
    try:
        document = json.loads((meta / EXIT_FILE).read_text(encoding="utf-8"))
    except FileNotFoundError:
        # O supervisor morreu junto (stop, SIGKILL): o executor terminou sem código relatado.
        return ExecutorStatus(ExecutorPhase.EXITED)
    exit_code = document.get("exit_code")
    return ExecutorStatus(
        ExecutorPhase.EXITED,
        exit_code=exit_code if isinstance(exit_code, int) else None,
        timed_out=document.get("timed_out") is True,
    )
