"""`page-map.json`: o mapa completo de páginas da execução (docs/GoblinShiftPlan.md, 1.5).

Entra no pacote como conteúdo, ao lado dos PDFs, e é a prévia do resultado sem renderizar PDF
(2.2). `schema_version` 1; mudanças só aditivas::

    {
      "schema_version": 1,
      "operation": "select",
      "inputs": [{"position": 0, "original_name": "a.pdf", "pages": 2}],
      "documents": [
        {"path": "selection.pdf",
         "pages": [{"input_position": 0, "page_number": 2}]}   # na ordem do documento
      ]
    }

Não traz `upload_id`, como o manifesto (3.6): as entradas são citadas pela posição. Fica bem
abaixo dos 8 MiB de 1.7: no máximo 400 páginas e 20 entradas.
"""

import json
from collections.abc import Sequence

from goblinshift.processing.pdf_press.composition import InputDocument, PlannedDocument
from goblinshift.processing.pdf_press.params import Operation

PAGE_MAP_NAME = "page-map.json"
PAGE_MAP_SCHEMA_VERSION = 1


def encode_page_map(
    operation: Operation,
    inputs: Sequence[InputDocument],
    documents: Sequence[tuple[str, PlannedDocument]],
) -> bytes:
    """`documents` traz o caminho final de cada documento (o que o `OutputDir` criou)."""
    document = {
        "schema_version": PAGE_MAP_SCHEMA_VERSION,
        "operation": operation.value,
        "inputs": [
            {
                "position": entry.position,
                "original_name": entry.original_name,
                "pages": entry.page_count,
            }
            for entry in inputs
        ],
        "documents": [
            {
                "path": path,
                "pages": [
                    {"input_position": page.position, "page_number": page.page_number}
                    for page in planned.pages
                ],
            }
            for path, planned in documents
        ],
    }
    return (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode()


def encode_document_preview(path: str, planned: PlannedDocument) -> bytes:
    """Preview de um documento (ADR 037): o trecho do mapa com as páginas dele, na ordem.

    Até 400 páginas, bem abaixo dos 64 KiB que a coleta aceita para um preview.
    """
    document = {
        "schema_version": PAGE_MAP_SCHEMA_VERSION,
        "path": path,
        "pages": [
            {"input_position": page.position, "page_number": page.page_number}
            for page in planned.pages
        ],
    }
    return (json.dumps(document, ensure_ascii=False) + "\n").encode()
