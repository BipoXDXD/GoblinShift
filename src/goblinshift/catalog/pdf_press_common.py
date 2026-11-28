"""Peças do pdf-press comuns às versões do catálogo (ADR 035 e ADR 070).

Ficam fora de `pdf_press_v1` e `pdf_press_v2` para que retirar uma versão não quebre a outra.
"""

from collections.abc import Mapping, Sequence
from typing import Annotated
from uuid import UUID

from pydantic import Field

from goblinshift.catalog.references import UploadReference
from goblinshift.processing.pdf_press import MAX_INPUT_PAGES

type PageNumber = Annotated[int, Field(ge=1, le=MAX_INPUT_PAGES)]


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
