"""Previews das receitas: derivados pequenos para a interface mostrar o resultado (2.2, 3.6).

São opcionais: a receita guarda os bytes em memória enquanto processa e os grava depois de todo
o conteúdo, só enquanto cabem nos tetos de 1.7 (220 arquivos e 480 MiB somados) e no teto do
formato, que a coleta do worker também confere. O conteúdo nunca perde espaço para um preview, e
faltar espaço nunca falha a execução: o preview só não existe.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import PurePosixPath

from goblinshift.processing.recipe import ItemOk, OutputDir, OutputGroup, OutputRole, Preview

# Tetos por formato (2.2), pela extensão do nome: a amostra CSV e o mapa de páginas (JSON) têm no
# máximo 64 KiB; o JPEG, 512 KiB. A coleta do worker recusa a execução acima deles.
IMAGE_PREVIEW_MAX_BYTES = 512 * 1024
TABLE_PREVIEW_MAX_BYTES = 64 * 1024
PREVIEW_MAX_BYTES: Mapping[str, int] = {
    ".jpg": IMAGE_PREVIEW_MAX_BYTES,
    ".csv": TABLE_PREVIEW_MAX_BYTES,
    ".json": TABLE_PREVIEW_MAX_BYTES,
}


@dataclass(frozen=True)
class PendingPreview:
    position: int
    # Saída de conteúdo do item da qual o preview deriva.
    of: str
    logical_name: str
    content: bytes


def write_previews(
    out: OutputDir, pending: Iterable[PendingPreview]
) -> Mapping[int, tuple[Preview, ...]]:
    """Grava na ordem recebida o que cabe no teto do formato e no espaço que resta; devolve os
    gravados por posição de entrada."""
    written: dict[int, tuple[Preview, ...]] = {}
    for preview in pending:
        size = len(preview.content)
        if size > _max_bytes(preview.logical_name) or not out.room_for(size):
            continue
        output = out.create(
            preview.logical_name, OutputRole.PREVIEW, preview.position, OutputGroup.PREVIEWS
        )
        with output.stream as stream:
            stream.write(preview.content)
        written[preview.position] = (
            *written.get(preview.position, ()),
            Preview(output.name, preview.of),
        )
    return written


def _max_bytes(logical_name: str) -> int:
    extension = PurePosixPath(logical_name).suffix
    limit = PREVIEW_MAX_BYTES.get(extension)
    if limit is None:
        raise ValueError(f"preview com extensão fora da lista: {logical_name!r}")
    return limit


def attach_previews[I](items: Sequence[I], previews: Mapping[int, tuple[Preview, ...]]) -> list[I]:
    """Os itens aceitos com os previews gravados para a sua posição; os demais como vieram."""
    return [
        replace(item, previews=previews[item.position])
        if isinstance(item, ItemOk) and item.position in previews
        else item
        for item in items
    ]
