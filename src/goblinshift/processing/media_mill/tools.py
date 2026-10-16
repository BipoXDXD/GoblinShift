"""Execução do FFmpeg e do ffprobe com limites (docs/GoblinShiftPlan.md, 2.3 e 3.6; ADR 036).

Quem monta a linha de comando é `commands`; aqui só se executa, sempre do mesmo jeito:

- binário por caminho absoluto e ambiente vazio: nada de `PATH` do processo, e variáveis como
  `FFREPORT` (que faria o FFmpeg gravar um relatório onde quisesse) nunca chegam ao filho;
- `stdin` em `/dev/null` (além do `-nostdin`), `stdout` e `stderr` em arquivos do diretório de
  rascunho, para a memória do executor não crescer com a saída de um arquivo hostil;
- tempo: cada chamada tem um prazo, o menor entre o teto dela e o que sobra do orçamento da
  execução inteira (`TimeBudget`), abaixo dos 10 minutos da tentativa (1.7). Estourar levanta
  `ToolTimeoutError`; a receita decide se recusa o vídeo ou falha a execução;
- memória e tamanho de arquivo: `RLIMIT_AS` e `RLIMIT_FSIZE` no filho, abaixo dos 2 GiB do
  container, para um decodificador descontrolado morrer sozinho sem levar o executor junto.
"""

import resource
import shutil
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from goblinshift.processing.recipe import FailureCode, RecipeFailure

MIB = 1024 * 1024
GIB = 1024 * MIB
# Uma tentativa tem 10 min (1.7); o resto fica para o relatório, o manifesto e o ZIP.
DEFAULT_BUDGET_S = 8 * 60
# Um processo do FFmpeg com uma thread de decodificação de 1080p cabe com folga em 1 GiB.
CHILD_ADDRESS_SPACE_BYTES = 1 * GIB
# O maior arquivo que o FFmpeg escreve é o WAV de 300 s, estéreo, 48 kHz, 16 bits (~55 MiB);
# o log do stderr também é arquivo e fica sob o mesmo teto.
CHILD_FILE_SIZE_BYTES = 256 * MIB


class ToolMissingError(RuntimeError):
    """FFmpeg ou ffprobe ausente: defeito da imagem do executor, não do conteúdo."""


@dataclass(frozen=True)
class Tools:
    ffmpeg: Path
    ffprobe: Path

    @classmethod
    def discover(cls) -> Tools:
        """Os binários do `PATH` do executor (na imagem, o build fixado em /usr/local/bin)."""
        return cls(ffmpeg=_absolute_binary("ffmpeg"), ffprobe=_absolute_binary("ffprobe"))


def _absolute_binary(name: str) -> Path:
    found = shutil.which(name)
    if found is None:
        raise ToolMissingError(f"{name} não está instalado")
    return Path(found).resolve()


class ToolTimeoutError(Exception):
    """A chamada passou do prazo; quem chamou decide se é o vídeo ou a execução que falha."""


class TimeBudget:
    """Orçamento de tempo da execução inteira, medido com relógio monotônico."""

    def __init__(self, total_s: float, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._deadline = clock() + total_s

    def timeout(self, cap_s: float) -> float:
        """Prazo da próxima chamada: o teto dela ou o que sobra do orçamento, o menor."""
        remaining = self._deadline - self._clock()
        if remaining <= 0:
            raise budget_exhausted()
        return min(cap_s, remaining)


@dataclass(frozen=True)
class Outcome:
    returncode: int  # negativo: morto por sinal (por exemplo, SIGXFSZ ou SIGKILL)
    stdout: Path
    log: Path

    @property
    def succeeded(self) -> bool:
        return self.returncode == 0


def run_tool(argv: Sequence[str], *, timeout_s: float, scratch: Path, name: str) -> Outcome:
    """Roda um comando já montado, com `stdout` e `stderr` em `scratch/<name>.out|.log`."""
    stdout_path = scratch / f"{name}.out"
    log_path = scratch / f"{name}.log"
    with stdout_path.open("xb") as stdout, log_path.open("xb") as log:
        try:
            completed = subprocess.run(  # noqa: S603 - argv montado por `commands`, sem shell
                argv,
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=log,
                env={},
                timeout=timeout_s,
                close_fds=True,
                check=False,
                # O executor é um processo de uma thread só; é o caso em que preexec_fn é seguro.
                preexec_fn=_limit_child,
            )
        except subprocess.TimeoutExpired:
            # O `subprocess.run` já matou (SIGKILL) e esperou o filho.
            raise ToolTimeoutError(f"{name} passou de {timeout_s:.1f} s") from None
    return Outcome(completed.returncode, stdout_path, log_path)


def _limit_child() -> None:
    resource.setrlimit(resource.RLIMIT_FSIZE, (CHILD_FILE_SIZE_BYTES, CHILD_FILE_SIZE_BYTES))
    # O macOS não permite baixar o RLIMIT_AS (setrlimit falha com EINVAL); o executor só roda em
    # Linux, sob gVisor, e o macOS fica restrito aos testes locais da receita.
    if sys.platform == "linux":
        resource.setrlimit(
            resource.RLIMIT_AS, (CHILD_ADDRESS_SPACE_BYTES, CHILD_ADDRESS_SPACE_BYTES)
        )


def budget_exhausted() -> RecipeFailure:
    return RecipeFailure(
        FailureCode.RESOURCE_LIMIT_EXCEEDED,
        "a conversão dos vídeos passou do tempo disponível na tentativa",
    )
