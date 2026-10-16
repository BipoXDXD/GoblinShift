"""Escrita de um documento planejado com o qpdf, via pikepdf (ADR 035).

O documento é novo: só as páginas copiadas, sem catálogo, marcadores, estrutura de
acessibilidade nem formulários da origem (1.5 não promete preservá-los). O qpdf copia cada página
com os recursos que ela usa; referências a páginas que não foram copiadas viram `null`, então um
link não arrasta para a saída uma página que a pessoa não escolheu.

Opções de gravação:
- `stream_decode_level=none`: os streams vão como estão, sem descomprimir; uma bomba de
  descompressão num stream nunca é expandida aqui;
- `deterministic_id=True`: o /ID sai do conteúdo, e as mesmas entradas dão os mesmos bytes (3.6);
- `min_version`: a maior versão entre as entradas que contribuem, para a saída não declarar
  versão menor que a dos recursos copiados.
"""

from collections.abc import Mapping
from typing import BinaryIO

import pikepdf

from goblinshift.processing.pdf_press.composition import PlannedDocument
from goblinshift.processing.pdf_press.inspection import InspectedDocument


def write_document(
    planned: PlannedDocument, sources: Mapping[int, InspectedDocument], sink: BinaryIO
) -> None:
    """Grava `planned` em `sink`; `sources` são as entradas abertas, pela posição."""
    with pikepdf.new() as composed:
        for page in planned.pages:
            composed.pages.append(sources[page.position].pdf.pages[page.page_number - 1])
        composed.save(
            sink,
            min_version=_highest_version(planned, sources),
            stream_decode_level=pikepdf.StreamDecodeLevel.none,
            deterministic_id=True,
        )


def _highest_version(planned: PlannedDocument, sources: Mapping[int, InspectedDocument]) -> str:
    versions = {sources[page.position].pdf_version for page in planned.pages}
    return max(versions, key=_version_key)


def _version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))
