"""Manifesto do pacote, `schema_version` 1 (3.6). Mudanças só aditivas.

O manifesto é determinístico: depende só do relatório, do `PackageSpec` e dos digests das
saídas. Fica de fora tudo o que muda entre duas execuções iguais ou expõe a infraestrutura:
`run_id`, `upload_id`, horários, caminhos reais (`/work`), o hash do próprio manifesto, o do
`report.json` e o do ZIP; esses hashes ficam nos metadados do `Artifact`. `details` e `counts`
vão só para o `report.json`.
"""

import json
from collections.abc import Mapping, Sequence
from typing import assert_never

from goblinshift.processing.packaging.spec import MANIFEST_SCHEMA_VERSION, FileDigest, PackageSpec
from goblinshift.processing.recipe import (
    InputFile,
    ItemDuplicate,
    ItemOk,
    ItemRejected,
    Report,
    ReportItem,
    ReportWarning,
)


def encode_manifest(report: Report, spec: PackageSpec, digests: Mapping[str, FileDigest]) -> bytes:
    """Manifesto em UTF-8. `digests` tem exatamente as saídas citadas nos `ItemOk`.

    Relatório que não casa com as entradas, ou digests que não casam com as saídas, é erro de
    programação do executor (`ValueError`), nunca falha da receita.
    """
    inputs = _inputs_by_position(report, spec)
    outputs = [
        (item.position, path)
        for item in report.items
        if isinstance(item, ItemOk)
        for path in item.outputs
    ]
    _require_digests_for(outputs, digests)
    document = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "recipe": {
            "id": spec.recipe_id,
            "version": spec.recipe_version,
            "executor_image": spec.executor_image,
        },
        "parameters": _sorted_keys(spec.parameters),
        "inputs": [_input(item, inputs[item.position]) for item in report.items],
        "outputs": [
            {
                "path": path,
                "size": digests[path].size,
                "sha256": digests[path].sha256,
                "source_position": position,
            }
            for position, path in outputs
        ],
        "warnings": _warnings(report.warnings),
    }
    return (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode()


def _inputs_by_position(report: Report, spec: PackageSpec) -> dict[int, InputFile]:
    positions = [item.position for item in report.items]
    expected = [entry.position for entry in spec.inputs]
    if positions != expected:
        raise ValueError(f"relatório com posições {positions}; as entradas são {expected}")
    return {entry.position: entry for entry in spec.inputs}


def _require_digests_for(outputs: list[tuple[int, str]], digests: Mapping[str, FileDigest]) -> None:
    cited = {path for _, path in outputs}
    missing = sorted(cited - set(digests))
    unknown = sorted(set(digests) - cited)
    if missing or unknown:
        raise ValueError(f"digests não casam com as saídas: faltam {missing}, sobram {unknown}")


def _input(item: ReportItem, entry: InputFile) -> dict[str, object]:
    common: dict[str, object] = {
        "position": entry.position,
        "original_name": entry.original_name,
        "size": entry.size,
        "sha256": entry.sha256,
    }
    match item:
        case ItemOk():
            return {
                **common,
                "status": "ok",
                "outputs": list(item.outputs),
                "warnings": _warnings(item.warnings),
            }
        case ItemDuplicate():
            return {**common, "status": "duplicate", "duplicate_of": item.duplicate_of}
        case ItemRejected():
            return {**common, "status": "rejected", "code": item.code, "reason": item.reason}
        case unreachable:
            assert_never(unreachable)


def _sorted_keys(value: object) -> object:
    """Chaves em ordem em todos os níveis: os mesmos parâmetros dão os mesmos bytes."""
    if isinstance(value, Mapping):
        return {key: _sorted_keys(value[key]) for key in sorted(value)}
    if isinstance(value, list | tuple):
        return [_sorted_keys(element) for element in value]
    return value


def _warnings(warnings: Sequence[ReportWarning]) -> list[dict[str, str]]:
    return [{"code": warning.code, "detail": warning.detail} for warning in warnings]
