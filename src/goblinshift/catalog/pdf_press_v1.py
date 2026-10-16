"""Parâmetros do pdf-press v1 como modelo Pydantic estrito (docs/GoblinShiftPlan.md, 1.5; ADR 035).

As regras moram em `processing.pdf_press.Params`; o validador daqui só as chama, para o erro
apontar o campo sem duplicar a regra. Dentro da versão 1, schema, defaults e enums nunca mudam
(1.6).

Duas recusas moram aqui e não na biblioteca, porque dependem do que o cliente enviou de fato:
`pages_per_document` explícito fora do `split` (como o `name_prefix` do Detox, A13) e, na
aceitação, a página citada de um upload que não está em `input_upload_ids` (`upload_references`).
Por isso parâmetros já resolvidos não devem ser validados de novo por este modelo.
"""

from collections.abc import Mapping
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
    PageSelection,
    Params,
    params_from_mapping,
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
            select = tuple(PageSelection(str(p.upload_id), p.page_number) for p in self.select)
        return Params(self.operation, self.pages_per_document, select)


def upload_references(parameters: Mapping[str, object]) -> tuple[UploadReference, ...]:
    """Uploads citados pelo `select` dos parâmetros já resolvidos, com o campo de cada um."""
    selection = params_from_mapping(parameters).select or ()
    return tuple(
        UploadReference(f"select.{index}.upload_id", UUID(page.upload_id))
        for index, page in enumerate(selection)
    )
