"""Inspeção de um PDF de entrada com o qpdf, via pikepdf (docs/GoblinShiftPlan.md, 1.5; ADR 035).

Recusa, falhando a execução inteira:

- o que não começa com `%PDF-<versão>` no byte 0, o que o qpdf não abre sem recuperação
  (truncado, xref quebrado), documento sem páginas e qualquer aviso estrutural do qpdf, inclusive
  os que só aparecem ao ler os bytes de cada stream: `PDF_INVALID`;
- senha, criptografia, JavaScript, ações que não sejam `GoTo` ou `URI` (inclusive em `/OpenAction`
  e encadeadas em `/Next`), ações automáticas (`/AA`), formulários (AcroForm, XFA, widgets),
  assinaturas, anexos, multimídia e portfólios: `PDF_UNSUPPORTED_FEATURE`;
- páginas acima do que resta do teto da execução e objetos acima do teto por documento:
  `INPUT_LIMIT_EXCEEDED`.

A varredura passa por todos os objetos do arquivo, inclusive os que nenhuma página alcança: é
mais conservadora que olhar só o que será copiado. Não é sanitização: o documento recusado não é
consertado, e o aceito não fica certificado como inofensivo. Os detalhes citam a entrada pela
posição (a partir de 1), nunca o nome nem o caminho, e nunca repassam a mensagem do qpdf, que
traz o nome do arquivo.
"""

import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Self

import pikepdf
from pikepdf import Array, Dictionary, Stream

from goblinshift.processing.pdf_press.limits import Limits
from goblinshift.processing.recipe import FailureCode, InputFile, RecipeFailure

# O cabeçalho no byte 0, sem tolerar lixo antes (o qpdf aceitaria com aviso).
_HEADER = re.compile(rb"%PDF-[12]\.[0-9]")
_HEADER_BYTES = 8
_ALLOWED_ACTIONS = frozenset({"/GoTo", "/URI"})
# Onde um dicionário guarda uma ação; `/AA` é recusado inteiro, por disparar sozinho.
_ACTION_KEYS = ("/A", "/OpenAction", "/Next")
_MULTIMEDIA_SUBTYPES = frozenset({"/Movie", "/Sound", "/Screen", "/RichMedia", "/3D"})
_SIGNATURE_KEYS = frozenset({"/Perms", "/ByteRange", "/SigFlags"})
_SIGNATURE_TYPES = frozenset({"/Sig", "/DocTimeStamp"})
_FORM_KEYS = frozenset({"/AcroForm", "/XFA", "/FT"})
_ATTACHMENT_KEYS = frozenset({"/EmbeddedFiles", "/EF", "/AF"})
_JAVASCRIPT_KEYS = frozenset({"/JS", "/JavaScript"})


@dataclass
class InspectedDocument:
    """PDF aberto e conferido; fica aberto até a composição copiar as páginas."""

    pdf: pikepdf.Pdf
    page_count: int
    pdf_version: str

    def close(self) -> None:
        self.pdf.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()


def inspect_document(entry: InputFile, page_budget: int, limits: Limits) -> InspectedDocument:
    """Abre e confere a entrada; `page_budget` é quanto ainda cabe no teto de páginas."""
    where = f"entrada {entry.position + 1}"
    _require_header(entry.path, where)
    pdf = _open(entry.path, where)
    try:
        page_count = _checked_page_count(pdf, where, page_budget, limits)
    except BaseException:
        pdf.close()
        raise
    return InspectedDocument(pdf, page_count, pdf.pdf_version)


def _require_header(path: Path, where: str) -> None:
    with path.open("rb") as source:
        header = source.read(_HEADER_BYTES)
    if not _HEADER.fullmatch(header):
        raise RecipeFailure(FailureCode.PDF_INVALID, f"{where}: não começa com %PDF-<versão>")


def _open(path: Path, where: str) -> pikepdf.Pdf:
    try:
        # Sem recuperação: o que o qpdf precisaria reconstruir é arquivo danificado (1.5).
        return pikepdf.open(path, attempt_recovery=False)
    except pikepdf.PasswordError:
        raise _unsupported(where, ["protegido por senha"]) from None
    except pikepdf.PdfError:
        raise _invalid(where, "arquivo danificado") from None


def _checked_page_count(pdf: pikepdf.Pdf, where: str, page_budget: int, limits: Limits) -> int:
    try:
        if pdf.is_encrypted:
            raise _unsupported(where, ["criptografado"])
        _require_object_count(int(pdf.trailer.get("/Size", 0)), where, limits)
        page_count = _page_count(pdf, where, page_budget)
        objects = pdf.objects
        _require_object_count(len(objects), where, limits)
        reasons = _unsupported_reasons(objects)
        if reasons:
            raise _unsupported(where, reasons)
        _read_every_stream(objects)
    except pikepdf.PdfError:
        raise _invalid(where, "arquivo danificado") from None
    if pdf.get_warnings():
        raise _invalid(where, "aviso estrutural do qpdf")
    return page_count


def _require_object_count(count: int, where: str, limits: Limits) -> None:
    if count > limits.max_objects_per_document:
        raise RecipeFailure(
            FailureCode.INPUT_LIMIT_EXCEEDED,
            f"{where}: mais de {limits.max_objects_per_document} objetos",
        )


def _page_count(pdf: pikepdf.Pdf, where: str, page_budget: int) -> int:
    count = len(pdf.pages)
    if count == 0:
        raise _invalid(where, "documento sem páginas")
    if count > page_budget:
        raise RecipeFailure(
            FailureCode.INPUT_LIMIT_EXCEEDED,
            f"{where}: {count} páginas passam do limite de páginas de entrada da execução",
        )
    return count


def _unsupported_reasons(objects: Iterable[pikepdf.Object]) -> list[str]:
    """Motivos distintos, na ordem em que aparecem; vazio quando nada foi encontrado."""
    found: dict[str, None] = {}
    for root in objects:
        for node in _direct_tree(root):
            for reason in _reasons_of(node):
                found.setdefault(reason, None)
    return list(found)


def _direct_tree(root: pikepdf.Object) -> Iterator[pikepdf.Object]:
    """O objeto e os dicionários diretos aninhados nele; os indiretos têm a vez deles na lista."""
    stack = [root]
    while stack:
        node = stack.pop()
        if isinstance(node, Dictionary | Stream):
            yield node
            children = list(node.values())
        elif isinstance(node, Array):
            children = list(node)
        else:
            continue
        stack.extend(
            child
            for child in children
            if isinstance(child, Dictionary | Array) and not child.is_indirect
        )


def _reasons_of(node: pikepdf.Object) -> Iterator[str]:
    keys = set(node.keys())
    subtype = str(node.get("/Subtype", ""))
    kind = str(node.get("/Type", ""))
    if keys & _JAVASCRIPT_KEYS:
        yield "JavaScript"
    if keys & _SIGNATURE_KEYS or kind in _SIGNATURE_TYPES or str(node.get("/FT", "")) == "/Sig":
        yield "assinatura digital"
    if keys & _FORM_KEYS or subtype == "/Widget":
        yield "formulário"
    if keys & _ATTACHMENT_KEYS or kind == "/EmbeddedFile" or subtype == "/FileAttachment":
        yield "anexo"
    if "/AA" in keys:
        yield "ação automática (/AA)"
    if subtype in _MULTIMEDIA_SUBTYPES:
        yield "multimídia"
    if "/Collection" in keys:
        yield "portfólio de PDFs"
    for key in _ACTION_KEYS:
        if key in keys:
            yield from (f"ação {kind}" for kind in _disallowed_actions(node[key]))


def _disallowed_actions(value: pikepdf.Object) -> Iterator[str]:
    """Tipos (`/S`) fora da lista permitida; `/OpenAction` com destino (array) não é ação."""
    actions = value if isinstance(value, Array) else [value]
    for action in actions:
        if isinstance(action, Dictionary | Stream) and "/S" in action:
            kind = str(action.get("/S"))
            if kind not in _ALLOWED_ACTIONS:
                yield kind


def _read_every_stream(objects: Iterable[pikepdf.Object]) -> None:
    """Lê os bytes crus (sem decodificar) de cada stream: um /Length errado vira aviso aqui."""
    for candidate in objects:
        if isinstance(candidate, Stream):
            candidate.read_raw_bytes()


def _invalid(where: str, reason: str) -> RecipeFailure:
    return RecipeFailure(FailureCode.PDF_INVALID, f"{where}: {reason}")


def _unsupported(where: str, reasons: list[str]) -> RecipeFailure:
    return RecipeFailure(FailureCode.PDF_UNSUPPORTED_FEATURE, f"{where}: {', '.join(reasons)}")
