"""Escrita de um documento planejado com o qpdf, via pikepdf (ADR 035).

O documento é novo: só as páginas copiadas, sem catálogo, marcadores, estrutura de
acessibilidade nem formulários da origem (1.5 não promete preservá-los). O qpdf copia cada página
com os recursos que ela usa; referências a páginas que não foram copiadas viram `null`, então um
link não arrasta para a saída uma página que a pessoa não escolheu.

O documento novo também sai sem XMP (`/Metadata`), `/PieceInfo` e `/LastModified` em qualquer
dicionário ou stream copiado, de página ou de XObject, inclusive de recurso compartilhado entre
páginas; a entrada aberta não muda. O `/Info` do trailer e o XMP do catálogo das entradas nunca
chegam aqui: o documento nasce de `pikepdf.new()`, com trailer e catálogo próprios.

Na v2 (ADR 070):
- rotação: cada página planejada com `rotation` é girada de forma relativa, somada ao `/Rotate`
  que ela já tinha, inclusive o herdado da árvore de páginas, que o qpdf resolve ao copiar;
- `strip_annotation_metadata`: tira autor (`/T`) e datas (`/M`, `/CreationDate`) de cada
  anotação das páginas e do `/Popup` dela; o texto da nota (`/Contents`) fica. Sem a opção, a
  anotação fica como está. As URIs dos links ficam como estão;
- compressão sem perdas (com um `FlateRecompressor`): a composição é gravada primeiro num
  contador de bytes, com as opções da v1, e esse é o tamanho "antes"; depois o Flate é
  recomprimido e o documento é gravado com object streams (o qpdf sobe a versão para 1.5).

Opções de gravação:
- `stream_decode_level=none`: os streams vão como estão, sem descomprimir; uma bomba de
  descompressão num stream nunca é expandida aqui;
- `deterministic_id=True`: o /ID sai do conteúdo, e as mesmas entradas dão os mesmos bytes (3.6);
- `min_version`: a maior versão entre as entradas que contribuem, para a saída não declarar
  versão menor que a dos recursos copiados.
"""

import io
from collections.abc import Buffer, Mapping
from dataclasses import dataclass
from typing import BinaryIO, cast

import pikepdf

from goblinshift.processing.pdf_press.composition import PlannedDocument
from goblinshift.processing.pdf_press.compression import FlateRecompressor, RecompressionStats
from goblinshift.processing.pdf_press.inspection import InspectedDocument

_PRIVATE_METADATA_KEYS = ("/Metadata", "/PieceInfo", "/LastModified")
_ANNOTATION_METADATA_KEYS = ("/T", "/M", "/CreationDate")


@dataclass(frozen=True)
class WrittenDocument:
    # Tamanho da mesma composição gravada sem compressão; None quando não se pediu compressão.
    bytes_uncompressed: int | None
    bytes_written: int


class DocumentWriter:
    """Grava os documentos planejados de uma execução; uma instância por execução.

    Sem `recompressor`, grava como a v1. Com ele, comprime sem perdas e acumula em
    `recompression` os streams recomprimidos e mantidos de todos os documentos.
    """

    def __init__(
        self, *, strip_annotation_metadata: bool, recompressor: FlateRecompressor | None = None
    ) -> None:
        self._strip_annotation_metadata = strip_annotation_metadata
        self._recompressor = recompressor
        self.recompression = RecompressionStats(recompressed=0, kept=0)

    def write(
        self, planned: PlannedDocument, sources: Mapping[int, InspectedDocument], sink: BinaryIO
    ) -> WrittenDocument:
        """Grava `planned` em `sink`; `sources` são as entradas abertas, pela posição."""
        with pikepdf.new() as composed:
            for page in planned.pages:
                composed.pages.append(sources[page.position].pdf.pages[page.page_number - 1])
                if page.rotation:
                    composed.pages[-1].rotate(page.rotation, relative=True)
            _drop_private_metadata(composed)
            if self._strip_annotation_metadata:
                _drop_annotation_metadata(composed)
            version = _highest_version(planned, sources)
            written = _ByteCounter(sink)
            if self._recompressor is None:
                _save(composed, written, version)
                return WrittenDocument(None, written.count)
            uncompressed = _ByteCounter(None)
            _save(composed, uncompressed, version)
            self.recompression += self._recompressor.recompress(composed)
            _save(composed, written, version, pikepdf.ObjectStreamMode.generate)
            return WrittenDocument(uncompressed.count, written.count)


def _save(
    pdf: pikepdf.Pdf,
    sink: io.RawIOBase,
    version: str,
    object_streams: pikepdf.ObjectStreamMode = pikepdf.ObjectStreamMode.preserve,
) -> None:
    pdf.save(
        # O pikepdf só chama write(); o contador não buscável basta (sem linearizar).
        cast(BinaryIO, sink),
        min_version=version,
        stream_decode_level=pikepdf.StreamDecodeLevel.none,
        deterministic_id=True,
        object_stream_mode=object_streams,
    )


class _ByteCounter(io.RawIOBase):
    """Conta os bytes gravados e os repassa a `target`; sem `target`, só conta.

    Não é buscável: o qpdf grava em sequência quando não linearizado.
    """

    def __init__(self, target: BinaryIO | None) -> None:
        super().__init__()
        self._target = target
        self.count = 0

    def writable(self) -> bool:
        return True

    def write(self, data: Buffer, /) -> int:
        view = memoryview(data)
        if self._target is not None:
            self._target.write(view)
        self.count += view.nbytes
        return view.nbytes


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


def _drop_annotation_metadata(pdf: pikepdf.Pdf) -> None:
    """Apaga autor e datas de cada anotação das páginas de `pdf` e do `/Popup` dela."""
    for page in pdf.pages:
        for annotation in page.obj.get("/Annots", pikepdf.Array()):
            if not isinstance(annotation, pikepdf.Dictionary):
                continue
            popup = annotation.get("/Popup")
            targets = [annotation]
            if isinstance(popup, pikepdf.Dictionary):
                targets.append(popup)
            for target in targets:
                for key in _ANNOTATION_METADATA_KEYS:
                    if key in target:
                        del target[key]


def _highest_version(planned: PlannedDocument, sources: Mapping[int, InspectedDocument]) -> str:
    versions = {sources[page.position].pdf_version for page in planned.pages}
    return max(versions, key=_version_key)


def _version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))
