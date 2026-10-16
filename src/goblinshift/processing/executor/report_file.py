"""Serialização de `report.json` e de `telemetry.json` (schemas documentados no pacote do
executor)."""

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import assert_never

from goblinshift.processing.executor.job import Job
from goblinshift.processing.recipe import (
    FailureCode,
    ItemDuplicate,
    ItemOk,
    ItemRejected,
    RecipeFailure,
    Report,
    ReportItem,
    ReportWarning,
)

MIB = 1024 * 1024
REPORT_SCHEMA_VERSION = 1
REPORT_MAX_BYTES = 8 * MIB  # 1.7: 8 MiB para cada relatório ou manifesto
TIMINGS_SCHEMA_VERSION = 1
# Fases medidas pelo executor (3.7); o worker mede a coleta e a publicação.
TIMING_PHASES = ("inspect_ms", "process_ms", "package_ms")


def write_report(path: Path, report: Report, job: Job, max_bytes: int = REPORT_MAX_BYTES) -> None:
    """Grava o relatório com criação exclusiva; acima do teto, falha deterministicamente."""
    data = encode_report(report, job)
    if len(data) > max_bytes:
        raise RecipeFailure(
            FailureCode.REPORT_LIMIT_EXCEEDED,
            f"o relatório teria {len(data)} bytes; o limite é {max_bytes}",
        )
    with path.open("xb") as target:
        target.write(data)


def encode_report(report: Report, job: Job) -> bytes:
    names = {entry.position: entry.original_name for entry in job.inputs}
    ok = [item for item in report.items if isinstance(item, ItemOk)]
    rejected = [item for item in report.items if isinstance(item, ItemRejected)]
    document = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "recipe_id": job.recipe_id,
        "recipe_version": job.recipe_version,
        "summary": {
            "inputs": len(report.items),
            "ok": len(ok),
            "rejected": len(rejected),
            "duplicates": len(report.items) - len(ok) - len(rejected),
            "outputs": sum(len(item.outputs) for item in ok),
        },
        "items": [_item(item, names[item.position]) for item in report.items],
        "warnings": _warnings(report.warnings),
        "details": dict(report.details),
    }
    return (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode()


def _item(item: ReportItem, original_name: str) -> dict[str, object]:
    match item:
        case ItemOk():
            return {
                "position": item.position,
                "original_name": original_name,
                "status": "ok",
                "outputs": list(item.outputs),
                "counts": dict(item.counts),
                "details": dict(item.details),
                "warnings": _warnings(item.warnings),
                "notices": [notice.value for notice in item.notices],
            }
        case ItemRejected():
            return {
                "position": item.position,
                "original_name": original_name,
                "status": "rejected",
                "code": item.code,
                "reason": item.reason,
            }
        case ItemDuplicate():
            return {
                "position": item.position,
                "original_name": original_name,
                "status": "duplicate",
                "duplicate_of": item.duplicate_of,
            }
        case unreachable:
            assert_never(unreachable)


def _warnings(warnings: Sequence[ReportWarning]) -> list[dict[str, str]]:
    return [{"code": warning.code, "detail": warning.detail} for warning in warnings]


def write_timings(path: Path, timings: Mapping[str, int]) -> None:
    """Grava os tempos das fases medidas, em milissegundos, com criação exclusiva.

    Fica na raiz da área de trabalho, fora de `out/`: é telemetria para o worker, não entra no
    pacote nem no manifesto, e o pacote continua determinístico.
    """
    document = {"schema_version": TIMINGS_SCHEMA_VERSION, **timings}
    with path.open("x", encoding="utf-8") as target:
        json.dump(document, target)
