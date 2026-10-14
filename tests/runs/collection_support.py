"""Construtores do `out/` que o executor deixa, para os testes da coleta (3.6, ADR 019)."""

import hashlib
import json
import warnings
import zipfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from goblinshift.processing.packaging import MANIFEST_NAME, MIB, REPORT_NAME, compression_for
from goblinshift.processing.packaging.zip_writer import ZipSource, write_zip

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


def cited_outputs(out: Path) -> list[str]:
    report = json.loads((out / "report.json").read_bytes())
    return [name for item in report["items"] if item["status"] == "ok" for name in item["outputs"]]


def manifest_for(out: Path) -> dict[str, Any]:
    """Manifesto coerente com o conteúdo de out/, com os campos que o empacotador escreve."""
    return {
        "schema_version": 1,
        "recipe": {"id": RECIPE, "version": 1, "executor_image": None},
        "parameters": {},
        "inputs": [_manifest_input(item) for item in _report_items(out)],
        "outputs": [
            {
                "path": name,
                "size": len((out / name).read_bytes()),
                "sha256": hashlib.sha256((out / name).read_bytes()).hexdigest(),
                "source_position": 0,
            }
            for name in cited_outputs(out)
        ],
        "warnings": [],
    }


def _report_items(out: Path) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = json.loads((out / "report.json").read_bytes())["items"]
    return items


def _manifest_input(item: dict[str, Any]) -> dict[str, Any]:
    """A entrada do manifesto que o empacotador escreve para o item do relatório."""
    common = {
        "position": item["position"],
        "original_name": item["original_name"],
        "size": 1,
        "sha256": "0" * 64,
        "status": item["status"],
    }
    match item["status"]:
        case "ok":
            return {**common, "outputs": item["outputs"], "warnings": item["warnings"]}
        case "rejected":
            return {**common, "code": item["code"], "reason": item["reason"]}
        case _:
            return {**common, "duplicate_of": item["duplicate_of"]}


def write_manifest(out: Path, manifest: object) -> None:
    (out / MANIFEST_NAME).unlink(missing_ok=True)
    (out / MANIFEST_NAME).write_bytes(json.dumps(manifest, indent=2).encode())


def write_package(out: Path) -> None:
    """Manifesto e ZIP como o empacotador do executor os gravaria para o que já está em out/."""
    write_manifest(out, manifest_for(out))
    names = [*cited_outputs(out), MANIFEST_NAME, REPORT_NAME]
    write_zip(out / PACKAGE, [ZipSource(name, out / name) for name in names], 512 * MIB)


def rewrite_package(out: Path, entries: Sequence[tuple[zipfile.ZipInfo, bytes]]) -> None:
    """Troca o ZIP de out/ por um montado à mão (entradas fora do contrato)."""
    (out / PACKAGE).unlink()
    with zipfile.ZipFile(out / PACKAGE, "w") as archive, warnings.catch_warnings():
        # Entrada repetida é um dos casos fora do contrato; o zipfile só avisa ao gravá-la.
        warnings.simplefilter("ignore", UserWarning)
        for info, data in entries:
            archive.writestr(info, data)


def entry(
    name: str, data: bytes, *, method: int | None = None, mode: int = 0o100644
) -> tuple[zipfile.ZipInfo, bytes]:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.create_system = 3
    info.external_attr = mode << 16
    info.compress_type = compression_for(name) if method is None else method
    return info, data


def package_entries(out: Path) -> list[tuple[zipfile.ZipInfo, bytes]]:
    """As entradas que o empacotador gravaria, para os testes alterarem uma delas."""
    names = [*cited_outputs(out), MANIFEST_NAME, REPORT_NAME]
    return [entry(name, (out / name).read_bytes()) for name in names]
