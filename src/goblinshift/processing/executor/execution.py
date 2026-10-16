"""Laço do executor: lê o job, confere as entradas, roda a receita e traduz o desfecho.

É um dos dois lugares que traduzem exceções em classes de falha (docs/GoblinShiftPlan.md, 1.6),
aqui como código de saída do processo; o contrato está no docstring do pacote.
"""

import csv
import errno
import json
import sys
import time
import traceback
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from enum import IntEnum
from pathlib import Path
from types import TracebackType
from typing import Self

from goblinshift.processing.csv_janitor import Limits
from goblinshift.processing.executor.job import (
    InputIntegrityError,
    Job,
    JobError,
    read_context,
    read_job,
    verified_inputs,
    with_upload_ids,
)
from goblinshift.processing.executor.report_file import write_report, write_timings
from goblinshift.processing.image_forge.inspection import configure_pillow
from goblinshift.processing.output_dir import FileSystemOutputDir
from goblinshift.processing.packaging import (
    MANIFEST_NAME,
    REPORT_NAME,
    PackageLimits,
    PackageSpec,
    package,
)
from goblinshift.processing.recipe import InputFile, RecipeFailure, Report
from goblinshift.processing.registry import RECIPES, RegisteredRecipe

PACKAGE_LIMITS = PackageLimits()


class ExitCode(IntEnum):
    SUCCEEDED = 0
    # 1 e 2 ficam para o interpretador (exceção que escapou, uso inválido do `python -m`).
    RECIPE_FAILURE = 3
    INFRASTRUCTURE = 4
    INTEGRITY = 5
    # Disco da área de trabalho cheio (`ENOSPC`): limite de recurso de 1.6, como a memória. Sem
    # `failure.json`, porque não haveria onde gravá-lo.
    NO_SPACE = 6


class _WorkDir:
    def __init__(self, root: Path) -> None:
        self.job = root / "job.json"
        self.context = root / "context.json"
        self.inputs = root / "inputs"
        self.out = root / "out"
        self.progress = root / "progress.jsonl"
        self.failure = root / "failure.json"
        self.telemetry = root / "telemetry.json"


def main(argv: Sequence[str]) -> int:
    # `field_size_limit` é estado global do módulo csv e conta caracteres: ajustado uma vez,
    # como proteção grosseira; os limites em bytes ficam com a biblioteca (2.3).
    csv.field_size_limit(Limits().record_bytes)
    # Segunda barreira do Image Forge contra bomba de descompressão, também global ao processo;
    # a receita já recusa a imagem grande antes de decodificar e não depende dela.
    configure_pillow()
    if len(argv) != 1:
        print("uso: python -m goblinshift.processing.executor <work_dir>", file=sys.stderr)
        return ExitCode.INFRASTRUCTURE
    work = _WorkDir(Path(argv[0]))
    timings = _PhaseTimer()
    try:
        return _run(work, timings)
    finally:
        _write_timings(work.telemetry, timings)


def _run(work: _WorkDir, timings: _PhaseTimer) -> ExitCode:
    try:
        _execute(work, timings)
    except InputIntegrityError as error:
        print(f"executor: integridade: {error}", file=sys.stderr)
        return ExitCode.INTEGRITY
    except RecipeFailure as failure:
        _write_failure(work.failure, failure)
        return ExitCode.RECIPE_FAILURE
    except OSError as error:
        if error.errno == errno.ENOSPC:
            print("executor: disco da área de trabalho cheio (ENOSPC)", file=sys.stderr)
            return ExitCode.NO_SPACE
        traceback.print_exc()
        return ExitCode.INFRASTRUCTURE
    except Exception:
        # Fronteira do processo: o detalhe vai para o stderr (logs do container) e o worker
        # recebe só a classe da falha.
        traceback.print_exc()
        return ExitCode.INFRASTRUCTURE
    return ExitCode.SUCCEEDED


def _execute(work: _WorkDir, timings: _PhaseTimer) -> None:
    with timings.phase("inspect_ms"):
        recipe, inputs, spec, job = _inspect(work)
    _prepare_empty(work.out)
    out = FileSystemOutputDir(
        work.out, reserved_names=(MANIFEST_NAME, REPORT_NAME, spec.package_name)
    )
    with timings.phase("process_ms"):
        with _ProgressLog(work.progress) as progress:
            report = recipe.execute(job.parameters, inputs, out, progress)
        _require_one_item_per_input(report, inputs)
        write_report(work.out / REPORT_NAME, report, job)
    with timings.phase("package_ms"):
        # A falha do empacotador (teto do manifesto ou do ZIP) é determinística como a da receita.
        package(report, work.out, spec, PACKAGE_LIMITS)


def _inspect(work: _WorkDir) -> tuple[RegisteredRecipe, Sequence[InputFile], PackageSpec, Job]:
    job = read_job(work.job)
    context = read_context(work.context)
    recipe = RECIPES.get((job.recipe_id, job.recipe_version))
    if recipe is None:
        raise JobError(f"receita desconhecida: {job.recipe_id} v{job.recipe_version}")
    inputs = with_upload_ids(verified_inputs(work.inputs, job.inputs), context.input_upload_ids)
    spec = PackageSpec(
        recipe_id=job.recipe_id,
        recipe_version=job.recipe_version,
        executor_image=context.executor_image,
        parameters=job.parameters,
        inputs=inputs,
    )
    return recipe, inputs, spec, job


def _prepare_empty(out: Path) -> None:
    """Saídas de outra tentativa nunca se misturam às desta."""
    out.mkdir(exist_ok=True)
    if any(out.iterdir()):
        raise JobError(f"{out.name}/ já tem arquivos")


def _require_one_item_per_input(report: Report, inputs: Sequence[InputFile]) -> None:
    positions = [item.position for item in report.items]
    expected = [entry.position for entry in inputs]
    if positions != expected:
        raise RuntimeError(f"relatório com posições {positions}; as entradas são {expected}")


def _write_failure(path: Path, failure: RecipeFailure) -> None:
    document = {"code": failure.code.value, "detail": failure.detail}
    with path.open("x", encoding="utf-8") as target:
        json.dump(document, target, ensure_ascii=False)


class _PhaseTimer:
    """Duração de cada fase concluída, em milissegundos, pelo relógio monotônico."""

    def __init__(self) -> None:
        self.measured: dict[str, int] = {}

    @contextmanager
    def phase(self, name: str) -> Iterator[None]:
        started = time.monotonic()
        yield
        self.measured[name] = round((time.monotonic() - started) * 1000)


def _write_timings(path: Path, timings: _PhaseTimer) -> None:
    """Telemetria é acessória: falhar ao gravá-la nunca muda o desfecho da tentativa."""
    try:
        write_timings(path, timings.measured)
    except OSError as error:
        print(f"executor: tempos das fases não gravados: {error}", file=sys.stderr)


class _ProgressLog:
    """`progress.jsonl`: uma linha por entrada avaliada, visível ao worker assim que escrita."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def __enter__(self) -> Self:
        self._file = self._path.open("x", encoding="utf-8")
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self._file.close()

    def item_done(self, position: int) -> None:
        self._file.write(json.dumps({"position": position}) + "\n")
        self._file.flush()
