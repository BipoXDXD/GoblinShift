"""Receita csv-janitor, versão 1: `clean_table` sobre o contrato de 3.2.

Cada tabela aceita vira um CSV com nome derivado do original; cada tabela recusada vira
`ItemRejected` com o código da biblioteca. Rejeitar todas não é falha da receita: o relatório
completo volta ao executor, e o worker decide `failed` com `NO_VALID_OUTPUTS` na publicação
(docs/GoblinShiftPlan.md, 3.5), sem perder o motivo de cada rejeição.
"""

import unicodedata
from collections.abc import Sequence
from dataclasses import asdict
from typing import assert_never

from goblinshift.processing.csv_janitor.janitor import clean_table
from goblinshift.processing.csv_janitor.limits import Limits
from goblinshift.processing.csv_janitor.params import Params
from goblinshift.processing.csv_janitor.results import TableCleaned, TableRejected
from goblinshift.processing.recipe import (
    InputContractError,
    InputFile,
    ItemOk,
    ItemRejected,
    OutputDir,
    OutputRole,
    Progress,
    Report,
    ReportWarning,
)

RECIPE_ID = "csv-janitor"
VERSION = 1
MAX_TABLES = 5
OUTPUT_EXTENSION = ".csv"
FALLBACK_STEM = "tabela"
_UNSAFE_REPLACEMENT = "_"


class CsvJanitorV1:
    def __init__(self, limits: Limits | None = None, max_tables: int = MAX_TABLES) -> None:
        self._limits = limits or Limits()
        self._max_tables = max_tables

    def run(
        self, params: Params, inputs: Sequence[InputFile], out: OutputDir, progress: Progress
    ) -> Report:
        if len(inputs) > self._max_tables:
            raise InputContractError(
                f"{len(inputs)} tabelas; o limite por execução é {self._max_tables}"
            )
        items: list[ItemOk | ItemRejected] = []
        for table in inputs:
            items.append(self._clean_one(table, params, out))
            progress.item_done(table.position)
        return Report(items)

    def _clean_one(self, table: InputFile, params: Params, out: OutputDir) -> ItemOk | ItemRejected:
        with table.path.open("rb") as source:
            # Um byte além do limite basta para a biblioteca rejeitar a tabela pelo tamanho.
            data = source.read(self._limits.table_bytes + 1)
        match clean_table(data, params, self._limits):
            case TableCleaned() as cleaned:
                output = out.create(
                    output_name(table.original_name), OutputRole.CONTENT, table.position
                )
                with output.stream as stream:
                    stream.write(cleaned.content)
                return ItemOk(
                    position=table.position,
                    outputs=(output.name,),
                    warnings=tuple(ReportWarning(w.code, w.detail) for w in cleaned.warnings),
                    counts=asdict(cleaned.counts),
                )
            case TableRejected() as rejected:
                return ItemRejected(table.position, rejected.code, rejected.reason)
            case unreachable:
                assert_never(unreachable)


def output_name(original_name: str) -> str:
    """Radical do nome original, sem caracteres que não cabem num componente, com `.csv`.

    O nome original é dado, nunca caminho: separadores e controles viram `_`, e pontos iniciais
    saem para a saída não virar arquivo oculto nem `..`.
    """
    stem, dot, _ = original_name.rpartition(".")
    if not dot or not stem:
        stem = original_name
    safe = "".join(_UNSAFE_REPLACEMENT if _is_unsafe(char) else char for char in stem)
    return (safe.lstrip(".") or FALLBACK_STEM) + OUTPUT_EXTENSION


def _is_unsafe(char: str) -> bool:
    return char in ("/", "\\") or unicodedata.category(char) == "Cc"
