"""Parâmetros do PDF Press (docs/GoblinShiftPlan.md, 1.5; ADR 035).

`Params` nasce válido. Duas recusas só cabem ao catálogo, que sabe o que o cliente enviou:
`pages_per_document` explícito fora do `split` e a referência a um upload que não está entre as
entradas da execução. Aqui chegam os parâmetros já resolvidos, com o default de
`pages_per_document` sempre presente e `select` nulo fora do `select`.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from goblinshift.processing.pdf_press.limits import MAX_INPUT_PAGES, MAX_OUTPUT_PAGES

DEFAULT_PAGES_PER_DOCUMENT = 1
# Forma canônica que o catálogo grava (UUID em minúsculas, com hífens); nenhuma outra chega aqui.
_CANONICAL_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


class InvalidParametersError(ValueError):
    """Configuração recusada antes de ler qualquer arquivo."""


class Operation(StrEnum):
    MERGE = "merge"  # concatena as entradas, na ordem, num PDF só
    SPLIT = "split"  # cada entrada vira documentos de `pages_per_document` páginas
    SELECT = "select"  # um PDF com as páginas de `select`, na ordem, repetições incluídas


@dataclass(frozen=True)
class PageSelection:
    """Uma página escolhida: o upload da execução e o número da página, a partir de 1."""

    upload_id: str
    page_number: int

    def __post_init__(self) -> None:
        if not _CANONICAL_UUID.fullmatch(self.upload_id):
            raise InvalidParametersError("upload_id deve ser um UUID na forma canônica")
        if not 1 <= self.page_number <= MAX_INPUT_PAGES:
            raise InvalidParametersError(f"page_number deve estar entre 1 e {MAX_INPUT_PAGES}")


@dataclass(frozen=True)
class Params:
    operation: Operation = Operation.MERGE
    # Usado só no `split`: 1 separa página a página; N, em grupos de N (o último pode ser menor).
    pages_per_document: int = DEFAULT_PAGES_PER_DOCUMENT
    # Só no `select`, obrigatório e não vazio; nulo nas outras operações.
    select: tuple[PageSelection, ...] | None = None

    def __post_init__(self) -> None:
        if not 1 <= self.pages_per_document <= MAX_INPUT_PAGES:
            raise InvalidParametersError(
                f"pages_per_document deve estar entre 1 e {MAX_INPUT_PAGES}"
            )
        if self.operation is not Operation.SELECT:
            if self.select is not None:
                raise InvalidParametersError("select só se aplica à operação select")
            return
        if not self.select:
            raise InvalidParametersError("select precisa de ao menos uma página")
        if len(self.select) > MAX_OUTPUT_PAGES:
            raise InvalidParametersError(f"select aceita no máximo {MAX_OUTPUT_PAGES} páginas")


_KNOWN_OPTIONS = frozenset({"operation", "pages_per_document", "select"})
_SELECTION_KEYS = frozenset({"upload_id", "page_number"})


def params_from_mapping(raw: Mapping[str, object]) -> Params:
    """Converte a configuração decodificada de JSON; recusa campos desconhecidos e tipos errados."""
    unknown = sorted(set(raw) - _KNOWN_OPTIONS)
    if unknown:
        raise InvalidParametersError(f"opções desconhecidas: {unknown}")
    return Params(
        operation=_operation(raw.get("operation", Operation.MERGE.value)),
        pages_per_document=_integer(
            raw.get("pages_per_document", DEFAULT_PAGES_PER_DOCUMENT), "pages_per_document"
        ),
        select=_selection(raw.get("select")),
    )


def _operation(raw: object) -> Operation:
    if isinstance(raw, str) and raw in Operation:
        return Operation(raw)
    allowed = [member.value for member in Operation]
    raise InvalidParametersError(f"operation deve ser um de {allowed}")


def _selection(raw: object) -> tuple[PageSelection, ...] | None:
    if raw is None:
        return None
    if not isinstance(raw, Sequence) or isinstance(raw, str):
        raise InvalidParametersError("select deve ser uma lista")
    return tuple(_page(item) for item in raw)


def _page(raw: object) -> PageSelection:
    if not isinstance(raw, Mapping) or set(raw) != _SELECTION_KEYS:
        raise InvalidParametersError("cada item de select tem exatamente upload_id e page_number")
    upload_id = raw["upload_id"]
    if not isinstance(upload_id, str):
        raise InvalidParametersError("upload_id deve ser texto")
    return PageSelection(upload_id, _integer(raw["page_number"], "page_number"))


def _integer(raw: object, option: str) -> int:
    # bool é subclasse de int em Python, mas `true` não é número no JSON.
    if not isinstance(raw, int) or isinstance(raw, bool):
        raise InvalidParametersError(f"{option} deve ser inteiro")
    return raw
