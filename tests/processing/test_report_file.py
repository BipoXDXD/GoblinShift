"""Teto de bytes do relatório (docs/GoblinShiftPlan.md, 1.7 e 3.6)."""

import json
from pathlib import Path
from typing import Any

import pytest

from goblinshift.processing.executor.job import InputEntry, Job
from goblinshift.processing.executor.report_file import encode_report, write_report, write_timings
from goblinshift.processing.recipe import (
    FailureCode,
    ItemDuplicate,
    ItemOk,
    ItemRejected,
    RecipeFailure,
    Report,
)

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


# --- Fatia 5: duplicatas e detalhes (aditivo, schema_version continua 1) ----------------------

DETOX_JOB = Job(
    recipe_id="downloads-detox",
    recipe_version=1,
    parameters={},
    inputs=(
        InputEntry(0, "0000.bin", "Foto.png", 3, "a" * 64),
        InputEntry(1, "0001.bin", "foto (1).png", 3, "a" * 64),
        InputEntry(2, "0002.bin", "x.html", 5, "b" * 64),
    ),
)
DETOX_REPORT = Report(
    items=[
        ItemOk(0, ("images/Foto.png",), details={"kind": "image"}),
        ItemDuplicate(1, 0),
        ItemRejected(2, "UNSUPPORTED_FORMAT", "HTML"),
    ],
    details={"note": "ZIP opaco"},
)


def _decoded(report: Report, job: Job) -> dict[str, Any]:
    document: dict[str, Any] = json.loads(encode_report(report, job))
    return document


def test_duplicata_vira_item_com_status_duplicate_e_a_posicao_original() -> None:
    document = _decoded(DETOX_REPORT, DETOX_JOB)

    assert document["items"][1] == {
        "position": 1,
        "original_name": "foto (1).png",
        "status": "duplicate",
        "duplicate_of": 0,
    }


def test_resumo_conta_duplicatas_separadas_das_rejeicoes() -> None:
    document = _decoded(DETOX_REPORT, DETOX_JOB)

    assert document["summary"] == {
        "inputs": 3,
        "ok": 1,
        "rejected": 1,
        "duplicates": 1,
        "outputs": 1,
    }


def test_detalhes_do_item_e_da_execucao_vao_para_o_relatorio() -> None:
    document = _decoded(DETOX_REPORT, DETOX_JOB)

    assert document["items"][0]["details"] == {"kind": "image"}
    assert document["details"] == {"note": "ZIP opaco"}


def test_item_aceito_tem_exatamente_as_chaves_do_schema() -> None:
    document = _decoded(DETOX_REPORT, DETOX_JOB)

    assert set(document["items"][0]) == {
        "position",
        "original_name",
        "status",
        "outputs",
        "counts",
        "details",
        "warnings",
        "notices",
    }
    assert set(document) == {
        "schema_version",
        "recipe_id",
        "recipe_version",
        "summary",
        "items",
        "warnings",
        "details",
    }
    assert document["schema_version"] == 1


def test_tempos_gravados_com_versao_e_so_as_fases_medidas(tmp_path: Path) -> None:
    write_timings(tmp_path / "telemetry.json", {"inspect_ms": 12, "process_ms": 340})

    document = json.loads((tmp_path / "telemetry.json").read_text(encoding="utf-8"))
    assert document == {"schema_version": 1, "inspect_ms": 12, "process_ms": 340}


def test_tempos_nao_sobrescrevem_arquivo_existente(tmp_path: Path) -> None:
    (tmp_path / "telemetry.json").write_text("{}", encoding="utf-8")

    with pytest.raises(FileExistsError):
        write_timings(tmp_path / "telemetry.json", {"inspect_ms": 1})
