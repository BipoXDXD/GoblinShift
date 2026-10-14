"""Nome lógico e grupo de cada saída do Detox (1.4 e 3.6).

Daqui sai só o nome pedido. NFC, colisões (sufixo pelo índice original), nomes reservados na
raiz e o teto de 180 bytes por componente são aplicados pelo `OutputDir`.
"""

from dataclasses import dataclass

from goblinshift.processing.downloads_detox.classify import Format, Kind
from goblinshift.processing.downloads_detox.params import GroupBy, NameMode, Params
from goblinshift.processing.names import extension_of, safe_stem
from goblinshift.processing.recipe import OutputGroup

FALLBACK_STEM = "file"
_GROUP = {
    Kind.IMAGE: OutputGroup.IMAGES,
    Kind.TABLE: OutputGroup.TABLES,
    Kind.TEXT: OutputGroup.TEXT,
    Kind.ARCHIVE: OutputGroup.ARCHIVES,
}


@dataclass(frozen=True)
class Destination:
    logical_name: str
    group: OutputGroup | None  # None é a raiz de out/


def destination(original_name: str, position: int, fmt: Format, params: Params) -> Destination:
    group = _GROUP[fmt.kind] if params.group_by is GroupBy.KIND else None
    return Destination(_logical_name(original_name, position, fmt, params), group)


def _logical_name(original_name: str, position: int, fmt: Format, params: Params) -> str:
    if params.name_mode is NameMode.INDEXED:
        # Índice original a partir de 1, mesmo com rejeições ou duplicatas antes (1.4).
        return f"{params.name_prefix}-{position + 1:03}{fmt.extension}"
    return safe_stem(original_name, FALLBACK_STEM) + extension_of(original_name)
