"""Contagem por coluna do aviso `CSV_FORMULA_CELLS`, do relatório ao banco e à API (1.3; ADR 039).

O relatório traz todas as colunas de saída com células que parecem fórmula; o banco guarda só as
`MAX_COLUMNS` de maior contagem (empate na ordem da saída) e quantas ficaram de fora. O nome da
coluna é dado do usuário: fica como texto, com teto de `MAX_COLUMN_NAME_CHARS` caracteres e sem
controle nem formato invisível (o JSONB do PostgreSQL recusa NUL, e bidi disfarça o texto
exibido). Cabeçalho vazio ou só de espaços vira nome nulo. Some com `run_input.details` na
retenção de 30 dias.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import TypeIs

from goblinshift.processing.names import has_unsafe_char

MAX_COLUMNS = 10
MAX_COLUMN_NAME_CHARS = 120
_ELLIPSIS = "…"
_REPLACEMENT = "\ufffd"  # U+FFFD, o caractere de substituição


@dataclass(frozen=True)
class ColumnCells:
    # `None` para cabeçalho vazio ou só de espaços: a tela dá o rótulo, que nunca colide com um
    # nome real, e a contagem da coluna não some.
    name: str | None
    cells: int


@dataclass(frozen=True)
class FormulaCells:
    columns: tuple[ColumnCells, ...]
    more_columns: int

    @classmethod
    def of(cls, columns: Iterable[tuple[str, int]]) -> FormulaCells:
        """Do relatório: maiores contagens primeiro, com o teto de colunas e de nome."""
        ranked = sorted(columns, key=lambda column: column[1], reverse=True)
        kept = tuple(
            ColumnCells(_display_name(name), cells) for name, cells in ranked[:MAX_COLUMNS]
        )
        return cls(kept, len(ranked) - len(kept))

    @classmethod
    def from_json(cls, raw: object) -> FormulaCells | None:
        """Do banco: o que não tem a forma de `as_json` vira ausência, sem derrubar o detalhe."""
        if not isinstance(raw, dict):
            return None
        columns, more = raw.get("columns"), raw.get("more_columns")
        if not isinstance(columns, list) or not 0 < len(columns) <= MAX_COLUMNS:
            return None
        if not _is_count(more, minimum=0):
            return None
        parsed = [_stored_column(column) for column in columns]
        if any(column is None for column in parsed):
            return None
        return cls(tuple(column for column in parsed if column is not None), more)

    def as_json(self) -> dict[str, object]:
        return {
            "columns": [{"name": c.name, "cells": c.cells} for c in self.columns],
            "more_columns": self.more_columns,
        }


def _display_name(name: str) -> str | None:
    if not name.strip():
        return None
    safe = "".join(_REPLACEMENT if has_unsafe_char(char) else char for char in name)
    if len(safe) <= MAX_COLUMN_NAME_CHARS:
        return safe
    return safe[: MAX_COLUMN_NAME_CHARS - len(_ELLIPSIS)] + _ELLIPSIS


def _stored_column(raw: object) -> ColumnCells | None:
    if not isinstance(raw, dict):
        return None
    name, cells = raw.get("name"), raw.get("cells")
    if name is not None and not _is_stored_name(name):
        return None
    return ColumnCells(name, cells) if _is_count(cells, minimum=1) else None


def _is_stored_name(name: object) -> bool:
    return isinstance(name, str) and bool(name.strip()) and len(name) <= MAX_COLUMN_NAME_CHARS


def _is_count(value: object, *, minimum: int) -> TypeIs[int]:
    return isinstance(value, int) and not isinstance(value, bool) and value >= minimum
