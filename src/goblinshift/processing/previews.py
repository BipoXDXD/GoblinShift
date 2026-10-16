"""Previews das receitas: derivados pequenos para a interface mostrar o resultado (2.2, 3.6).

São opcionais: a receita guarda os bytes em memória enquanto processa e os grava depois de todo
o conteúdo, só enquanto cabem nos tetos de 1.7 (220 arquivos e 480 MiB somados). O conteúdo nunca
perde espaço para um preview, e faltar espaço nunca falha a execução: o preview só não existe.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace

from goblinshift.processing.recipe import ItemOk, OutputDir, OutputGroup, OutputRole, Preview


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
    """Grava na ordem recebida enquanto há espaço; devolve os gravados por posição de entrada."""
    written: dict[int, tuple[Preview, ...]] = {}
    for preview in pending:
        if not out.room_for(len(preview.content)):
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


def attach_previews[I](items: Sequence[I], previews: Mapping[int, tuple[Preview, ...]]) -> list[I]:
    """Os itens aceitos com os previews gravados para a sua posição; os demais como vieram."""
    return [
        replace(item, previews=previews[item.position])
        if isinstance(item, ItemOk) and item.position in previews
        else item
        for item in items
    ]
