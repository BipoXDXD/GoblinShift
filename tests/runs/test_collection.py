"""Coleta de `out/`: só arquivos regulares e listados no relatório, dentro dos tetos (3.6, 1.7)."""

import json
import os
from pathlib import Path
from typing import Any

import pytest

from goblinshift.runs.lifecycle import ErrorCode, PublishOutcome
from goblinshift.runs.worker.collection import (
    CollectionError,
    OutputLimitError,
    OutputLimits,
    collect_outputs,
)


def ok_item(position: int, *outputs: str, warnings: int = 0) -> dict[str, Any]:
    return {
        "position": position,
        "original_name": f"{position}.csv",
        "status": "ok",
        "outputs": list(outputs),
        "counts": {},
        "warnings": [{"code": "W", "detail": "d"}] * warnings,
    }


def rejected_item(position: int, code: str = "CSV_STRUCTURE_INVALID") -> dict[str, Any]:
    return {
        "position": position,
        "original_name": f"{position}.csv",
        "status": "rejected",
        "code": code,
        "reason": "r",
    }


def write_out(out: Path, items: list[dict[str, Any]], files: dict[str, bytes]) -> None:
    out.mkdir()
    for name, data in files.items():
        (out / name).write_bytes(data)
    ok = [item for item in items if item["status"] == "ok"]
    report = {
        "schema_version": 1,
        "recipe_id": "csv-janitor",
        "recipe_version": 1,
        "summary": {
            "inputs": len(items),
            "ok": len(ok),
            "rejected": len(items) - len(ok),
            "outputs": sum(len(item["outputs"]) for item in ok),
        },
        "items": items,
        "warnings": [],
    }
    (out / "report.json").write_text(json.dumps(report), encoding="utf-8")


def test_coleta_saidas_na_ordem_do_relatorio(tmp_path: Path) -> None:
    out = tmp_path / "out"
    write_out(out, [ok_item(0, "b.csv"), ok_item(1, "a.csv")], {"a.csv": b"1", "b.csv": b"22"})

    collected = collect_outputs(out, input_count=2)

    assert [f.name for f in collected.outputs] == ["b.csv", "a.csv"]
    assert [f.size for f in collected.outputs] == [2, 1]
    assert collected.report.outcome is PublishOutcome.SUCCEEDED


@pytest.mark.parametrize(
    ("items", "files", "outcome"),
    [
        ([ok_item(0, "a.csv"), rejected_item(1)], {"a.csv": b"1"}, "succeeded_with_warnings"),
        ([ok_item(0, "a.csv", warnings=1)], {"a.csv": b"1"}, "succeeded_with_warnings"),
        ([rejected_item(0), rejected_item(1)], {}, "no_valid_outputs"),
    ],
)
def test_desfecho_sai_das_rejeicoes_e_avisos(
    tmp_path: Path, items: list[dict[str, Any]], files: dict[str, bytes], outcome: str
) -> None:
    out = tmp_path / "out"
    write_out(out, items, files)

    collected = collect_outputs(out, input_count=len(items))

    assert collected.report.outcome is PublishOutcome(outcome)


def test_symlink_em_out_e_recusado(tmp_path: Path) -> None:
    out = tmp_path / "out"
    (tmp_path / "secret").write_bytes(b"x")
    write_out(out, [ok_item(0, "a.csv")], {})
    os.symlink(tmp_path / "secret", out / "a.csv")

    with pytest.raises(CollectionError, match="regular"):
        collect_outputs(out, input_count=1)


def test_hardlink_em_out_e_recusado(tmp_path: Path) -> None:
    out = tmp_path / "out"
    (tmp_path / "outside").write_bytes(b"x")
    write_out(out, [ok_item(0, "a.csv")], {})
    os.link(tmp_path / "outside", out / "a.csv")

    with pytest.raises(CollectionError, match="hardlink"):
        collect_outputs(out, input_count=1)


def test_subdiretorio_em_out_e_recusado(tmp_path: Path) -> None:
    out = tmp_path / "out"
    write_out(out, [ok_item(0, "a.csv")], {"a.csv": b"1"})
    (out / "sub").mkdir()

    with pytest.raises(CollectionError, match="regular"):
        collect_outputs(out, input_count=1)


def test_excesso_de_arquivos_e_falha_deterministica(tmp_path: Path) -> None:
    out = tmp_path / "out"
    names = [f"{i}.csv" for i in range(4)]
    write_out(out, [ok_item(0, *names)], dict.fromkeys(names, b"1"))

    with pytest.raises(OutputLimitError) as caught:
        collect_outputs(out, input_count=1, limits=OutputLimits(max_files=3))

    assert caught.value.code is ErrorCode.OUTPUT_LIMIT_EXCEEDED


def test_quantidade_no_teto_e_aceita(tmp_path: Path) -> None:
    out = tmp_path / "out"
    names = [f"{i}.csv" for i in range(3)]
    write_out(out, [ok_item(0, *names)], dict.fromkeys(names, b"1"))

    collected = collect_outputs(out, input_count=1, limits=OutputLimits(max_files=3))

    assert len(collected.outputs) == 3


def test_excesso_de_bytes_e_falha_deterministica(tmp_path: Path) -> None:
    out = tmp_path / "out"
    write_out(out, [ok_item(0, "a.csv", "b.csv")], {"a.csv": b"12", "b.csv": b"345"})

    with pytest.raises(OutputLimitError) as caught:
        collect_outputs(out, input_count=1, limits=OutputLimits(max_bytes=4))

    assert caught.value.code is ErrorCode.OUTPUT_LIMIT_EXCEEDED


def test_relatorio_acima_do_teto_e_falha_deterministica(tmp_path: Path) -> None:
    out = tmp_path / "out"
    write_out(out, [ok_item(0, "a.csv")], {"a.csv": b"1"})

    with pytest.raises(OutputLimitError) as caught:
        collect_outputs(out, input_count=1, limits=OutputLimits(report_max_bytes=10))

    assert caught.value.code is ErrorCode.REPORT_LIMIT_EXCEEDED


def test_arquivo_fora_do_relatorio_e_recusado(tmp_path: Path) -> None:
    out = tmp_path / "out"
    write_out(out, [ok_item(0, "a.csv")], {"a.csv": b"1", "intruso.csv": b"2"})

    with pytest.raises(CollectionError, match="relatório"):
        collect_outputs(out, input_count=1)


def test_saida_citada_e_ausente_e_recusada(tmp_path: Path) -> None:
    out = tmp_path / "out"
    write_out(out, [ok_item(0, "a.csv")], {})

    with pytest.raises(CollectionError, match="relatório"):
        collect_outputs(out, input_count=1)


def test_relatorio_sem_um_item_por_entrada_e_recusado(tmp_path: Path) -> None:
    out = tmp_path / "out"
    write_out(out, [ok_item(0, "a.csv")], {"a.csv": b"1"})

    with pytest.raises(CollectionError, match="posições"):
        collect_outputs(out, input_count=2)


@pytest.mark.parametrize(
    "raw",
    [
        b"nao e json",
        b'{"schema_version": 2}',
        b'{"schema_version": 1, "schema_version": 1}',
        b"[]",
    ],
)
def test_relatorio_malformado_e_recusado(tmp_path: Path, raw: bytes) -> None:
    out = tmp_path / "out"
    out.mkdir()
    (out / "report.json").write_bytes(raw)

    with pytest.raises(CollectionError):
        collect_outputs(out, input_count=0)


def test_sem_relatorio_e_recusado(tmp_path: Path) -> None:
    out = tmp_path / "out"
    out.mkdir()

    with pytest.raises(CollectionError, match=r"report\.json"):
        collect_outputs(out, input_count=0)
