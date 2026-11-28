"""Parâmetros do pdf-press v1 como modelo Pydantic estrito (docs/GoblinShiftPlan.md, 1.5; ADR 035).

As regras moram em `processing.pdf_press.Params`; o validador daqui só as chama, para o erro
apontar o campo sem duplicar a regra. Dentro da versão 1, schema, defaults e enums nunca mudam
(1.6).

Duas recusas moram aqui e não na biblioteca, porque dependem do que o cliente enviou de fato:
`pages_per_document` explícito fora do `split` (como o `name_prefix` do Detox, A13) e, na
aceitação, a página citada de um upload que não está em `input_upload_ids` (`upload_references`).
Por isso parâmetros já resolvidos não devem ser validados de novo por este modelo.
"""

from collections.abc import Mapping, Sequence
from typing import Annotated, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, Strict, model_validator

from goblinshift.catalog.references import UploadReference
from goblinshift.processing.pdf_press import (
    DEFAULT_PAGES_PER_DOCUMENT,
    MAX_INPUT_PAGES,
    MAX_OUTPUT_PAGES,
    InvalidParametersError,
    Operation,
    PageRange,
    Params,
)

_STRICT = ConfigDict(extra="forbid", strict=True, frozen=True)

type PageNumber = Annotated[int, Field(ge=1, le=MAX_INPUT_PAGES)]


class PageSelectionParameters(BaseModel):
    """Uma página escolhida: o upload da execução e o número da página, a partir de 1."""

    model_config = _STRICT

    # O UUID chega como texto do JSON e sai na forma canônica (minúsculas, com hífens).
    upload_id: Annotated[UUID, Strict(False)]
    page_number: PageNumber


class PdfPressV1Parameters(BaseModel):
    model_config = ConfigDict(
        extra="forbid", strict=True, frozen=True, title="PdfPressV1Parameters"
    )

    operation: Annotated[Operation, Strict(False)] = Operation.MERGE
    # Só no split: 1 separa página a página; N, em grupos de N.
    pages_per_document: PageNumber = DEFAULT_PAGES_PER_DOCUMENT
    # Só no select, obrigatório: ordem da lista é a ordem do PDF; repetições são preservadas.
    select: list[PageSelectionParameters] | None = Field(
        default=None, min_length=1, max_length=MAX_OUTPUT_PAGES
    )

    @model_validator(mode="after")
    def _combination_rules(self) -> Self:
        if "pages_per_document" in self.model_fields_set and self.operation is not Operation.SPLIT:
            raise ValueError("pages_per_document só se aplica à operação split")
        try:
            self.to_params()
        except InvalidParametersError as error:
            raise ValueError(str(error)) from None
        return self

    def to_params(self) -> Params:
        select = None
        if self.select is not None:
            select = tuple(PageRange.single(str(p.upload_id), p.page_number) for p in self.select)
        return Params(self.operation, self.pages_per_document, select)


def upload_references(parameters: Mapping[str, object]) -> tuple[UploadReference, ...]:
    """Uploads citados pelo `select` dos parâmetros já resolvidos, com o campo de cada um.

    Lê só o `upload_id` de cada item do mapa canônico, sem o parser de uma versão: vale para a
    página da v1 e para o intervalo da v2 (ADR 070), com o mesmo resultado na v1.
    """
    selection = parameters.get("select") or ()
    if not isinstance(selection, Sequence) or isinstance(selection, str):
        raise TypeError("select resolvido não é uma lista")
    return tuple(
        UploadReference(("select", str(index), "upload_id"), UUID(_upload_id(item)))
        for index, item in enumerate(selection)
    )


def _upload_id(item: object) -> str:
    upload_id = item.get("upload_id") if isinstance(item, Mapping) else None
    if not isinstance(upload_id, str):
        raise TypeError("item de select resolvido sem upload_id")
    return upload_id
