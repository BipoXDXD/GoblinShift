"""Plano dos documentos de saída (docs/GoblinShiftPlan.md, 1.5; ADR 035).

Função pura: recebe os parâmetros e quantas páginas cada entrada tem e devolve, antes de escrever
qualquer byte, que documentos sairão e de que páginas, na ordem. Página inexistente e tetos de
saída falham aqui, para a execução nunca publicar uma composição incompleta.

Cada documento é atribuído à entrada da sua primeira página (`source_position`): é a linhagem
que o relatório e o manifesto registram, já que um PDF composto não pertence a uma entrada só.
"""

import math
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import assert_never

from goblinshift.processing.names import safe_stem
from goblinshift.processing.pdf_press.limits import Limits
from goblinshift.processing.pdf_press.params import Operation, PageSelection, Params
from goblinshift.processing.recipe import FailureCode, InputContractError, RecipeFailure

MERGED_NAME = "merged.pdf"
SELECTION_NAME = "selection.pdf"
PDF_EXTENSION = ".pdf"
STEM_FALLBACK = "documento"
# Até 200 páginas por entrada (1.5): três dígitos mantêm a ordem alfabética igual à das páginas.
PAGE_DIGITS = 3


@dataclass(frozen=True)
class InputDocument:
    """Uma entrada já aberta e conferida: só o que o plano precisa saber dela."""

    position: int
    original_name: str
    # Do context.json; None só se o worker não o escreveu (janela de deploy, ADR 021).
    upload_id: str | None
    page_count: int


@dataclass(frozen=True)
class SourcePage:
    position: int  # da entrada
    page_number: int  # a partir de 1


@dataclass(frozen=True)
class PlannedDocument:
    logical_name: str
    source_position: int
    pages: tuple[SourcePage, ...]


def plan_documents(
    params: Params, inputs: Sequence[InputDocument], limits: Limits
) -> tuple[PlannedDocument, ...]:
    """Documentos na ordem do ZIP. Falha com `RecipeFailure` antes de qualquer escrita."""
    match params.operation:
        case Operation.MERGE:
            plan: tuple[PlannedDocument, ...] = (_merged(inputs),)
        case Operation.SPLIT:
            plan = tuple(_split(inputs, params.pages_per_document))
        case Operation.SELECT:
            plan = (_selection(params.select or (), inputs),)
        case unreachable:
            assert_never(unreachable)
    _require_within_limits(plan, limits)
    return plan


def _merged(inputs: Sequence[InputDocument]) -> PlannedDocument:
    pages = tuple(
        SourcePage(entry.position, number)
        for entry in inputs
        for number in range(1, entry.page_count + 1)
    )
    return PlannedDocument(MERGED_NAME, inputs[0].position, pages)


def _split(inputs: Sequence[InputDocument], group_size: int) -> Iterator[PlannedDocument]:
    for entry in inputs:
        stem = safe_stem(entry.original_name, STEM_FALLBACK)
        for group in range(math.ceil(entry.page_count / group_size)):
            first = group * group_size + 1
            last = min(first + group_size - 1, entry.page_count)
            pages = tuple(SourcePage(entry.position, n) for n in range(first, last + 1))
            yield PlannedDocument(_part_name(stem, first, last), entry.position, pages)


def _part_name(stem: str, first: int, last: int) -> str:
    pages = f"p{first:0{PAGE_DIGITS}}"
    if last != first:
        pages += f"-{last:0{PAGE_DIGITS}}"
    return f"{stem}-{pages}{PDF_EXTENSION}"


def _selection(
    selection: Sequence[PageSelection], inputs: Sequence[InputDocument]
) -> PlannedDocument:
    by_upload = _inputs_by_upload(inputs)
    pages = tuple(_source_page(choice, by_upload) for choice in selection)
    return PlannedDocument(SELECTION_NAME, pages[0].position, pages)


def _inputs_by_upload(inputs: Sequence[InputDocument]) -> dict[str, InputDocument]:
    if any(entry.upload_id is None for entry in inputs):
        raise InputContractError("select exige o upload_id de cada entrada no context.json")
    return {entry.upload_id: entry for entry in inputs if entry.upload_id is not None}


def _source_page(choice: PageSelection, by_upload: dict[str, InputDocument]) -> SourcePage:
    entry = by_upload.get(choice.upload_id)
    if entry is None:
        raise InputContractError("select cita um upload que não está entre as entradas")
    if choice.page_number > entry.page_count:
        raise RecipeFailure(
            FailureCode.PDF_PAGE_NOT_FOUND,
            f"a entrada {entry.position + 1} tem {entry.page_count} páginas; "
            f"a seleção pede a página {choice.page_number}",
        )
    return SourcePage(entry.position, choice.page_number)


def _require_within_limits(plan: Sequence[PlannedDocument], limits: Limits) -> None:
    if len(plan) > limits.max_output_documents:
        raise RecipeFailure(
            FailureCode.OUTPUT_LIMIT_EXCEEDED,
            f"seriam {len(plan)} documentos; o limite é {limits.max_output_documents}",
        )
    total_pages = sum(len(document.pages) for document in plan)
    if total_pages > limits.max_output_pages:
        raise RecipeFailure(
            FailureCode.OUTPUT_LIMIT_EXCEEDED,
            f"seriam {total_pages} páginas de saída; o limite é {limits.max_output_pages}",
        )
