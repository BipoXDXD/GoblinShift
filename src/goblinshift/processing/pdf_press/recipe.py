"""Receita pdf-press, versão 1: inspecionar → planejar → compor → mapa de páginas.

Ao contrário das três receitas centrais, nenhuma entrada é rejeitada sozinha: arquivo danificado,
recurso não suportado, página inexistente ou limite estourado falham a execução inteira (1.5),
porque omitir um documento e publicar a composição incompleta pareceria sucesso. Tudo é
inspecionado e planejado antes do primeiro byte de saída.

Relatório: um `ItemOk` por entrada, com as páginas dela (`pages`) e quantas foram parar na saída
(`output_pages`, repetições incluídas). Cada documento aparece nas saídas da entrada da sua
primeira página; o `page-map.json`, nas da primeira entrada, antes dos PDFs dela. No `select`, a
entrada da qual nenhuma página foi escolhida leva o aviso `INPUT_NOT_SELECTED`.
"""

from collections import Counter
from collections.abc import Mapping, Sequence
from contextlib import ExitStack

from goblinshift.processing.pdf_press.composition import (
    InputDocument,
    PlannedDocument,
    plan_documents,
)
from goblinshift.processing.pdf_press.inspection import InspectedDocument, inspect_document
from goblinshift.processing.pdf_press.limits import MAX_INPUTS, RECIPE_ID, VERSION, Limits
from goblinshift.processing.pdf_press.page_map import PAGE_MAP_NAME, encode_page_map
from goblinshift.processing.pdf_press.params import Operation, Params
from goblinshift.processing.pdf_press.writing import write_document
from goblinshift.processing.recipe import (
    InputContractError,
    InputFile,
    ItemOk,
    OutputDir,
    OutputRole,
    Progress,
    Report,
    ReportItem,
    ReportWarning,
    require_max_inputs,
)

__all__ = ["RECIPE_ID", "VERSION", "PdfPressV1"]

NOT_SELECTED = ReportWarning("INPUT_NOT_SELECTED", "nenhuma página desta entrada foi selecionada")

type _Written = tuple[str, PlannedDocument]


class PdfPressV1:
    def __init__(self, max_inputs: int = MAX_INPUTS, limits: Limits | None = None) -> None:
        self._max_inputs = max_inputs
        self._limits = limits or Limits()

    def run(
        self, params: Params, inputs: Sequence[InputFile], out: OutputDir, progress: Progress
    ) -> Report:
        require_max_inputs(inputs, self._max_inputs, "PDFs")
        if not inputs:
            raise InputContractError("o PDF Press precisa de ao menos uma entrada")
        with ExitStack() as stack:
            opened = self._inspect_all(inputs, progress, stack)
            documents = [_described(entry, opened[entry.position]) for entry in inputs]
            plan = plan_documents(params, documents, self._limits)
            written = [(_write(planned, opened, out), planned) for planned in plan]
            versions = {position: source.pdf_version for position, source in opened.items()}
        page_map = out.create(PAGE_MAP_NAME, OutputRole.CONTENT, inputs[0].position)
        with page_map.stream as sink:
            sink.write(encode_page_map(params.operation, documents, written))
        return _report(params.operation, documents, written, versions, page_map.name)

    def _inspect_all(
        self, inputs: Sequence[InputFile], progress: Progress, stack: ExitStack
    ) -> dict[int, InspectedDocument]:
        """Abre e confere cada entrada, na ordem; o teto de páginas vale para a soma delas."""
        opened: dict[int, InspectedDocument] = {}
        page_budget = self._limits.max_input_pages
        for entry in inputs:
            document = stack.enter_context(inspect_document(entry, page_budget, self._limits))
            opened[entry.position] = document
            page_budget -= document.page_count
            progress.item_done(entry.position)
        return opened


def _described(entry: InputFile, document: InspectedDocument) -> InputDocument:
    return InputDocument(entry.position, entry.original_name, entry.upload_id, document.page_count)


def _write(
    planned: PlannedDocument, opened: Mapping[int, InspectedDocument], out: OutputDir
) -> str:
    output = out.create(planned.logical_name, OutputRole.CONTENT, planned.source_position)
    with output.stream as sink:
        write_document(planned, opened, sink)
    return output.name


def _report(
    operation: Operation,
    documents: Sequence[InputDocument],
    written: Sequence[_Written],
    versions: Mapping[int, str],
    page_map_name: str,
) -> Report:
    outputs: dict[int, list[str]] = {entry.position: [] for entry in documents}
    outputs[documents[0].position].append(page_map_name)
    for path, planned in written:
        outputs[planned.source_position].append(path)
    used = Counter(page.position for _, planned in written for page in planned.pages)
    items: list[ReportItem] = [
        ItemOk(
            entry.position,
            tuple(outputs[entry.position]),
            warnings=_warnings(operation, used[entry.position]),
            counts={"pages": entry.page_count, "output_pages": used[entry.position]},
            details={"pdf_version": versions[entry.position]},
        )
        for entry in documents
    ]
    details = {
        "operation": operation.value,
        "documents": len(written),
        "output_pages": sum(used.values()),
    }
    return Report(items, details=details)


def _warnings(operation: Operation, output_pages: int) -> tuple[ReportWarning, ...]:
    if operation is Operation.SELECT and output_pages == 0:
        return (NOT_SELECTED,)
    return ()
