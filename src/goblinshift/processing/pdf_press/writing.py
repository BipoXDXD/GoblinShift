"""Escrita de um documento planejado com o qpdf, via pikepdf (ADR 035).

O documento é novo: só as páginas copiadas, sem catálogo, marcadores, estrutura de
acessibilidade nem formulários da origem (1.5 não promete preservá-los). O qpdf copia cada página
com os recursos que ela usa; referências a páginas que não foram copiadas viram `null`, então um
link não arrasta para a saída uma página que a pessoa não escolheu.

O documento novo também sai sem XMP (`/Metadata`), `/PieceInfo` e `/LastModified` em qualquer
dicionário ou stream copiado, de página ou de XObject, inclusive de recurso compartilhado entre
páginas; a entrada aberta não muda. O `/T` de anotações e as URIs ficam como estão (decisão
pendente).

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

_PRIVATE_METADATA_KEYS = ("/Metadata", "/PieceInfo", "/LastModified")


def write_document(
    planned: PlannedDocument, sources: Mapping[int, InspectedDocument], sink: BinaryIO
) -> None:
    """Grava `planned` em `sink`; `sources` são as entradas abertas, pela posição."""
    with pikepdf.new() as composed:
        for page in planned.pages:
            composed.pages.append(sources[page.position].pdf.pages[page.page_number - 1])
        _drop_private_metadata(composed)
        composed.save(
            sink,
            min_version=_highest_version(planned, sources),
            stream_decode_level=pikepdf.StreamDecodeLevel.none,
            deterministic_id=True,
        )


def _drop_private_metadata(pdf: pikepdf.Pdf) -> None:
    """Apaga as chaves de `_PRIVATE_METADATA_KEYS` de todo dicionário e stream de `pdf`.

    `pdf.objects` traz só os objetos indiretos; a busca desce por dicionários, streams e arrays
    a partir de cada um para alcançar também os diretos (uma anotação dentro de `/Annots`, por
    exemplo). Cada indireto é visitado uma vez, pelo `objgen`, então ciclos não prendem o laço.
    """
    visited: set[tuple[int, int]] = set()
    pending: list[object] = list(pdf.objects)
    while pending:
        node = pending.pop()
        if not isinstance(node, pikepdf.Array | pikepdf.Dictionary | pikepdf.Stream):
            continue
        if node.is_indirect:
            if node.objgen in visited:
                continue
            visited.add(node.objgen)
        if isinstance(node, pikepdf.Array):
            pending.extend(node)
            continue
        for key in _PRIVATE_METADATA_KEYS:
            if key in node:
                del node[key]
        pending.extend(node.values())


def _highest_version(planned: PlannedDocument, sources: Mapping[int, InspectedDocument]) -> str:
    versions = {sources[page.position].pdf_version for page in planned.pages}
    return max(versions, key=_version_key)


def _version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))
