"""Construtores do `out/` que o executor deixa, para os testes da coleta (3.6, ADR 019)."""

import json
from pathlib import Path
from typing import Any

RECIPE = "csv-janitor"
PACKAGE = "csv-janitor.zip"


def ok_item(position: int, *outputs: str, warnings: int = 0) -> dict[str, Any]:
    return {
        "position": position,
        "original_name": f"{position}.csv",
        "status": "ok",
        "outputs": list(outputs),
        "counts": {},
        "warnings": [{"code": "W", "detail": "d"}] * warnings,
    }


def rejected_item(position: int, code: object = "CSV_STRUCTURE_INVALID") -> dict[str, Any]:
    return {
        "position": position,
        "original_name": f"{position}.csv",
        "status": "rejected",
        "code": code,
        "reason": "r",
    }


def duplicate_item(position: int, duplicate_of: object) -> dict[str, Any]:
    return {
        "position": position,
        "original_name": f"{position}.png",
        "status": "duplicate",
        "duplicate_of": duplicate_of,
    }


def write_out(out: Path, items: list[dict[str, Any]], files: dict[str, bytes]) -> None:
    out.mkdir()
    for name, data in files.items():
        (out / name).parent.mkdir(exist_ok=True)
        (out / name).write_bytes(data)
    write_report(out, items)


def summary_of(items: list[dict[str, Any]]) -> dict[str, int]:
    """O `summary` que o executor calcula dos itens."""
    ok = [item for item in items if item["status"] == "ok"]
    rejected = [item for item in items if item["status"] == "rejected"]
    return {
        "inputs": len(items),
        "ok": len(ok),
        "rejected": len(rejected),
        "duplicates": len(items) - len(ok) - len(rejected),
        "outputs": sum(len(item["outputs"]) for item in ok),
    }


def write_report(out: Path, items: list[dict[str, Any]], summary: object = None) -> None:
    report = {
        "schema_version": 1,
        "recipe_id": "csv-janitor",
        "recipe_version": 1,
        "summary": summary_of(items) if summary is None else summary,
        "items": items,
        "warnings": [],
    }
    (out / "report.json").write_text(json.dumps(report), encoding="utf-8")
