"""Teto de bytes do relatório (docs/GoblinShiftPlan.md, 1.7 e 3.6)."""

from pathlib import Path

import pytest

from goblinshift.processing.executor.job import InputEntry, Job
from goblinshift.processing.executor.report_file import encode_report, write_report
from goblinshift.processing.recipe import FailureCode, ItemRejected, RecipeFailure, Report

JOB = Job(
    recipe_id="csv-janitor",
    recipe_version=1,
    parameters={},
    inputs=(InputEntry(0, "0000.bin", "a.csv", 1, "0" * 64),),
)
REPORT = Report([ItemRejected(0, "CSV_STRUCTURE_INVALID", "aspas")])


def test_relatorio_no_teto_e_gravado(tmp_path: Path) -> None:
    size = len(encode_report(REPORT, JOB))

    write_report(tmp_path / "report.json", REPORT, JOB, max_bytes=size)

    assert (tmp_path / "report.json").stat().st_size == size


def test_relatorio_um_byte_acima_do_teto_falha_sem_gravar(tmp_path: Path) -> None:
    size = len(encode_report(REPORT, JOB))

    with pytest.raises(RecipeFailure) as failure:
        write_report(tmp_path / "report.json", REPORT, JOB, max_bytes=size - 1)

    assert failure.value.code is FailureCode.REPORT_LIMIT_EXCEEDED
    assert not (tmp_path / "report.json").exists()


def test_nunca_sobrescreve_um_relatorio_existente(tmp_path: Path) -> None:
    (tmp_path / "report.json").write_text("outro")

    with pytest.raises(FileExistsError):
        write_report(tmp_path / "report.json", REPORT, JOB)
