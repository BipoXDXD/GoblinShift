"""Receita downloads-detox, versão 1: classificar → deduplicar → nomear/agrupar → copiar bytes.

Tudo é decidido antes da primeira cópia, para o lote acima do teto falhar sem escrever nada.
Os bytes saem idênticos aos da entrada; o reaproveitamento do objeto no storage é do worker
(ADR 020), não desta receita. Duplicata e classificação não são aviso: nenhum item ganha
`warnings` no caso normal, e a execução não vira `succeeded_with_warnings` por elas (A14).
"""

import shutil
from collections.abc import Sequence
from dataclasses import dataclass
from typing import assert_never

from goblinshift.processing.downloads_detox.classify import Format, Kind, Refusal, classify
from goblinshift.processing.downloads_detox.naming import destination
from goblinshift.processing.downloads_detox.params import Params
from goblinshift.processing.recipe import (
    DetailValue,
    FailureCode,
    InputFile,
    ItemDuplicate,
    ItemOk,
    ItemRejected,
    OutputDir,
    OutputRole,
    Progress,
    RecipeFailure,
    Report,
    ReportItem,
    require_max_inputs,
)

RECIPE_ID = "downloads-detox"
VERSION = 1
MIB = 1024 * 1024
MAX_INPUTS = 100
# Teto do conteúdo de saída (1.7). Publicado no catálogo para a aceitação impor quando as cotas
# chegarem (A12); até lá, o lote que passar dele falha aqui com OUTPUT_LIMIT_EXCEEDED.
MAX_TOTAL_INPUT_BYTES = 480 * MIB
COPY_BLOCK_BYTES = 1 * MIB
REJECTION_CODE = "UNSUPPORTED_FORMAT"
OPAQUE_ARCHIVE_NOTE = "ZIP opaco: o Detox não abre o arquivo nem inspeciona o conteúdo"
OPAQUE_ARCHIVES_LIMIT = (
    "JAR, APK, DOCX e XLSX com extensão .zip passam como ZIP opaco; "
    "a classificação não certifica que um arquivo é inofensivo"
)


@dataclass(frozen=True)
class _Accepted:
    entry: InputFile
    fmt: Format


type _Decision = _Accepted | ItemRejected | ItemDuplicate


class DownloadsDetoxV1:
    def __init__(
        self, max_inputs: int = MAX_INPUTS, max_total_bytes: int = MAX_TOTAL_INPUT_BYTES
    ) -> None:
        self._max_inputs = max_inputs
        self._max_total_bytes = max_total_bytes

    def run(
        self, params: Params, inputs: Sequence[InputFile], out: OutputDir, progress: Progress
    ) -> Report:
        require_max_inputs(inputs, self._max_inputs, "arquivos")
        decisions = _decide(inputs, params)
        self._require_within_budget(decisions)
        items: list[ReportItem] = []
        for decision in decisions:
            items.append(_carry_out(decision, params, out))
            progress.item_done(items[-1].position)
        return Report(items, details={"opaque_archives": OPAQUE_ARCHIVES_LIMIT})

    def _require_within_budget(self, decisions: Sequence[_Decision]) -> None:
        total = sum(d.entry.size for d in decisions if isinstance(d, _Accepted))
        if total > self._max_total_bytes:
            raise RecipeFailure(
                FailureCode.OUTPUT_LIMIT_EXCEEDED,
                f"os arquivos aceitos somam {total} bytes; o limite é {self._max_total_bytes}",
            )


def _decide(inputs: Sequence[InputFile], params: Params) -> list[_Decision]:
    """Classifica cada entrada e marca as duplicatas; só entradas aceitas viram originais."""
    first_by_content: dict[tuple[str, int], int] = {}
    decisions: list[_Decision] = []
    for entry in inputs:
        with entry.path.open("rb") as source:
            classified = classify(source, entry.original_name)
        if isinstance(classified, Refusal):
            decisions.append(ItemRejected(entry.position, REJECTION_CODE, classified.reason))
            continue
        key = (entry.sha256, entry.size)
        if params.deduplicate and key in first_by_content:
            decisions.append(ItemDuplicate(entry.position, first_by_content[key]))
            continue
        first_by_content.setdefault(key, entry.position)
        decisions.append(_Accepted(entry, classified))
    return decisions


def _carry_out(decision: _Decision, params: Params, out: OutputDir) -> ReportItem:
    match decision:
        case _Accepted(entry, fmt):
            return _copy(entry, fmt, params, out)
        case ItemRejected() | ItemDuplicate():
            return decision
        case unreachable:
            assert_never(unreachable)


def _copy(entry: InputFile, fmt: Format, params: Params, out: OutputDir) -> ItemOk:
    target = destination(entry.original_name, entry.position, fmt, params)
    output = out.create(target.logical_name, OutputRole.CONTENT, entry.position, target.group)
    with entry.path.open("rb") as source, output.stream as sink:
        shutil.copyfileobj(source, sink, COPY_BLOCK_BYTES)
    return ItemOk(entry.position, (output.name,), details=_details(fmt))


def _details(fmt: Format) -> dict[str, DetailValue]:
    details: dict[str, DetailValue] = {"kind": fmt.kind.value}
    if fmt.kind is Kind.ARCHIVE:
        details["note"] = OPAQUE_ARCHIVE_NOTE
    return details
