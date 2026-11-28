"""Parâmetros do pdf-press v2 como modelo Pydantic estrito (docs/GoblinShiftPlan.md, 1.5; ADR 070).

A v2 troca a página avulsa do `select` por intervalos (`last_page` nulo é "até o fim", que só o
executor resolve) e acrescenta a rotação, a compressão sem perdas e o `strip_metadata`, ligado
por padrão. Como na v1, as regras moram em `processing.pdf_press`; os validadores daqui só as
chamam, para o erro apontar o campo. As recusas que dependem do que o cliente enviou de fato
(`pages_per_document` e `rotation` explícitos fora da operação deles) moram aqui. Schema e
defaults ficam congelados em `tests/catalog/congeladas/`.
"""

from collections.abc import Callable
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, Strict, model_validator

from goblinshift.catalog.pdf_press_v1 import PageNumber
from goblinshift.processing.pdf_press import (
    DEFAULT_PAGES_PER_DOCUMENT,
    MAX_OUTPUT_PAGES,
    Compression,
    InvalidParametersError,
    Operation,
    PageRange,
    Params,
)

_STRICT = ConfigDict(extra="forbid", strict=True, frozen=True)
# Os ângulos de `processing.pdf_press.ROTATIONS`, como literais para o schema publicar o enum.
type Rotation = Literal[0, 90, 180, 270]
_ROTATING_OPERATIONS = frozenset({Operation.MERGE, Operation.SPLIT})


class PageRangeParameters(BaseModel):
    """Páginas `first_page`..`last_page` de um upload; `last_page` nulo vai até o fim."""

    model_config = _STRICT

    # O UUID chega como texto do JSON e sai na forma canônica (minúsculas, com hífens).
    upload_id: Annotated[UUID, Strict(False)]
    first_page: PageNumber
    # Obrigatório, mesmo nulo: "até o fim" é sempre explícito.
    last_page: PageNumber | None
    rotation: Rotation = 0

    @model_validator(mode="after")
    def _library_rules(self) -> Self:
        _check(self.to_range)
        return self

    def to_range(self) -> PageRange:
        return PageRange(str(self.upload_id), self.first_page, self.last_page, self.rotation)


class PdfPressV2Parameters(BaseModel):
    model_config = ConfigDict(
        extra="forbid", strict=True, frozen=True, title="PdfPressV2Parameters"
    )

    operation: Annotated[Operation, Strict(False)] = Operation.MERGE
    # Só no split: 1 separa página a página; N, em grupos de N.
    pages_per_document: PageNumber = DEFAULT_PAGES_PER_DOCUMENT
    # Só no select, obrigatório: a ordem da lista é a ordem do PDF; repetições são preservadas.
    select: list[PageRangeParameters] | None = Field(
        default=None, min_length=1, max_length=MAX_OUTPUT_PAGES
    )
    # Só em merge e split; no select, cada intervalo tem a sua.
    rotation: Rotation = 0
    compression: Annotated[Compression, Strict(False)] = Compression.NONE
    strip_metadata: bool = True

    @model_validator(mode="after")
    def _combination_rules(self) -> Self:
        if "pages_per_document" in self.model_fields_set and self.operation is not Operation.SPLIT:
            raise ValueError("pages_per_document só se aplica à operação split")
        if "rotation" in self.model_fields_set and self.operation not in _ROTATING_OPERATIONS:
            raise ValueError("rotation só se aplica a merge e split")
        _check(self.to_params)
        return self

    def to_params(self) -> Params:
        select = None
        if self.select is not None:
            select = tuple(item.to_range() for item in self.select)
        return Params(
            self.operation,
            self.pages_per_document,
            select,
            self.rotation,
            self.compression,
            self.strip_metadata,
        )


def _check(build: Callable[[], object]) -> None:
    """Roda `build` e traduz a recusa da biblioteca no `ValueError` que o Pydantic põe no campo."""
    try:
        build()
    except InvalidParametersError as error:
        raise ValueError(str(error)) from None
