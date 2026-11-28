"""Parâmetros do PDF Press (docs/GoblinShiftPlan.md, 1.5; ADR 035 e ADR 070).

`Params` nasce válido. Duas recusas só cabem ao catálogo, que sabe o que o cliente enviou:
`pages_per_document` explícito fora do `split` e a referência a um upload que não está entre as
entradas da execução. Aqui chegam os parâmetros já resolvidos, com o default de
`pages_per_document` sempre presente e `select` nulo fora do `select`.

As duas versões usam o mesmo `Params`. O `select` é uma lista de intervalos: a v1 só manda
intervalos de uma página (`PageRange.single`), e a v2 manda intervalos abertos ("até o fim"), que
só o executor expande, porque só ele conhece a contagem de páginas. Os defaults daqui são o
comportamento da v1; os da v2 moram no catálogo.
"""

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from goblinshift.processing.pdf_press.limits import MAX_INPUT_PAGES, MAX_OUTPUT_PAGES

DEFAULT_PAGES_PER_DOCUMENT = 1
# Ângulos aceitos, em graus no sentido horário, como o `/Rotate` do PDF.
ROTATIONS = (0, 90, 180, 270)
NO_ROTATION = 0
# Forma canônica que o catálogo grava (UUID em minúsculas, com hífens); nenhuma outra chega aqui.
_CANONICAL_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


class InvalidParametersError(ValueError):
    """Configuração recusada antes de ler qualquer arquivo."""


class Operation(StrEnum):
    MERGE = "merge"  # concatena as entradas, na ordem, num PDF só
    SPLIT = "split"  # cada entrada vira documentos de `pages_per_document` páginas
    SELECT = "select"  # um PDF com as páginas de `select`, na ordem, repetições incluídas


class Compression(StrEnum):
    NONE = "none"  # grava como a v1
    LOSSLESS = "lossless"  # recomprime o Flate e gera object streams, sem mexer em imagens


@dataclass(frozen=True)
class PageRange:
    """Páginas `first_page`..`last_page` de um upload da execução, a partir de 1.

    `last_page` nulo é "até a última página", que o executor resolve depois de abrir o PDF.
    `rotation` gira cada página do intervalo, somada à rotação que ela já tem.
    """

    upload_id: str
    first_page: int
    last_page: int | None
    rotation: int = NO_ROTATION

    def __post_init__(self) -> None:
        if not _CANONICAL_UUID.fullmatch(self.upload_id):
            raise InvalidParametersError("upload_id deve ser um UUID na forma canônica")
        if not 1 <= self.first_page <= MAX_INPUT_PAGES:
            raise InvalidParametersError(f"first_page deve estar entre 1 e {MAX_INPUT_PAGES}")
        if self.last_page is not None and not self.first_page <= self.last_page <= MAX_INPUT_PAGES:
            raise InvalidParametersError(
                f"last_page deve estar entre first_page e {MAX_INPUT_PAGES}"
            )
        _require_rotation(self.rotation)

    @classmethod
    def single(cls, upload_id: str, page_number: int) -> PageRange:
        """Uma página só, sem rotação: o item do `select` da v1."""
        return cls(upload_id, page_number, page_number)


@dataclass(frozen=True)
class Params:
    operation: Operation = Operation.MERGE
    # Usado só no `split`: 1 separa página a página; N, em grupos de N (o último pode ser menor).
    pages_per_document: int = DEFAULT_PAGES_PER_DOCUMENT
    # Só no `select`, obrigatório e não vazio; nulo nas outras operações.
    select: tuple[PageRange, ...] | None = None
    # Só em `merge` e `split`: gira todas as páginas. No `select`, a rotação é a de cada intervalo.
    rotation: int = NO_ROTATION
    compression: Compression = Compression.NONE
    # Tira autor e datas das anotações (ADR 070).
    strip_metadata: bool = False

    def __post_init__(self) -> None:
        if not 1 <= self.pages_per_document <= MAX_INPUT_PAGES:
            raise InvalidParametersError(
                f"pages_per_document deve estar entre 1 e {MAX_INPUT_PAGES}"
            )
        _require_rotation(self.rotation)
        if self.operation is not Operation.SELECT:
            if self.select is not None:
                raise InvalidParametersError("select só se aplica à operação select")
            return
        if self.rotation != NO_ROTATION:
            raise InvalidParametersError(
                "rotation não se aplica ao select; use a rotation de cada intervalo"
            )
        if not self.select:
            raise InvalidParametersError("select precisa de ao menos uma página")
        if len(self.select) > MAX_OUTPUT_PAGES:
            raise InvalidParametersError(f"select aceita no máximo {MAX_OUTPUT_PAGES} páginas")


def _require_rotation(rotation: int) -> None:
    if rotation not in ROTATIONS:
        raise InvalidParametersError(f"rotation deve ser um de {list(ROTATIONS)}")


_KNOWN_OPTIONS = frozenset({"operation", "pages_per_document", "select"})
_SELECTION_KEYS = frozenset({"upload_id", "page_number"})
_KNOWN_OPTIONS_V2 = _KNOWN_OPTIONS | {"rotation", "compression", "strip_metadata"}
_RANGE_KEYS = frozenset({"upload_id", "first_page", "last_page", "rotation"})


def params_from_mapping(raw: Mapping[str, object]) -> Params:
    """Converte a configuração decodificada de JSON; recusa campos desconhecidos e tipos errados."""
    unknown = sorted(set(raw) - _KNOWN_OPTIONS)
    if unknown:
        raise InvalidParametersError(f"opções desconhecidas: {unknown}")
    return Params(
        operation=_operation(raw.get("operation", Operation.MERGE.value)),
        pages_per_document=_integer(
            raw.get("pages_per_document", DEFAULT_PAGES_PER_DOCUMENT), "pages_per_document"
        ),
        select=_selection(raw.get("select"), _page),
    )


def params_v2_from_mapping(raw: Mapping[str, object]) -> Params:
    """Como `params_from_mapping`, com os intervalos e as opções da v2 (ADR 070)."""
    unknown = sorted(set(raw) - _KNOWN_OPTIONS_V2)
    if unknown:
        raise InvalidParametersError(f"opções desconhecidas: {unknown}")
    return Params(
        operation=_operation(raw.get("operation", Operation.MERGE.value)),
        pages_per_document=_integer(
            raw.get("pages_per_document", DEFAULT_PAGES_PER_DOCUMENT), "pages_per_document"
        ),
        select=_selection(raw.get("select"), _page_range),
        rotation=_integer(raw.get("rotation", NO_ROTATION), "rotation"),
        compression=_compression(raw.get("compression", Compression.NONE.value)),
        strip_metadata=_boolean(raw.get("strip_metadata", False), "strip_metadata"),
    )


def _operation(raw: object) -> Operation:
    if isinstance(raw, str) and raw in Operation:
        return Operation(raw)
    allowed = [member.value for member in Operation]
    raise InvalidParametersError(f"operation deve ser um de {allowed}")


def _selection(raw: object, item: Callable[[object], PageRange]) -> tuple[PageRange, ...] | None:
    if raw is None:
        return None
    if not isinstance(raw, Sequence) or isinstance(raw, str):
        raise InvalidParametersError("select deve ser uma lista")
    return tuple(item(entry) for entry in raw)


def _page(raw: object) -> PageRange:
    """Item do `select` da v1: uma página, que vira um intervalo de uma página."""
    if not isinstance(raw, Mapping) or set(raw) != _SELECTION_KEYS:
        raise InvalidParametersError("cada item de select tem exatamente upload_id e page_number")
    page_number = _integer(raw["page_number"], "page_number")
    # A mensagem da v1 cita page_number, não o first_page do intervalo.
    if not 1 <= page_number <= MAX_INPUT_PAGES:
        raise InvalidParametersError(f"page_number deve estar entre 1 e {MAX_INPUT_PAGES}")
    return PageRange.single(_upload_id(raw["upload_id"]), page_number)


def _page_range(raw: object) -> PageRange:
    """Item do `select` da v2: `last_page` sempre presente, nulo para "até o fim"."""
    if not isinstance(raw, Mapping) or set(raw) != _RANGE_KEYS:
        raise InvalidParametersError(
            "cada item de select tem exatamente upload_id, first_page, last_page e rotation"
        )
    last_page = raw["last_page"]
    return PageRange(
        _upload_id(raw["upload_id"]),
        _integer(raw["first_page"], "first_page"),
        None if last_page is None else _integer(last_page, "last_page"),
        _integer(raw["rotation"], "rotation"),
    )


def _upload_id(raw: object) -> str:
    if not isinstance(raw, str):
        raise InvalidParametersError("upload_id deve ser texto")
    return raw


def _compression(raw: object) -> Compression:
    if isinstance(raw, str) and raw in Compression:
        return Compression(raw)
    allowed = [member.value for member in Compression]
    raise InvalidParametersError(f"compression deve ser um de {allowed}")


def _boolean(raw: object, option: str) -> bool:
    if not isinstance(raw, bool):
        raise InvalidParametersError(f"{option} deve ser booleano")
    return raw


def _integer(raw: object, option: str) -> int:
    # bool é subclasse de int em Python, mas `true` não é número no JSON.
    if not isinstance(raw, int) or isinstance(raw, bool):
        raise InvalidParametersError(f"{option} deve ser inteiro")
    return raw
