"""O que a limpeza de uma tabela devolve: a tabela limpa ou a rejeição dela inteira."""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum


class RejectionCode(StrEnum):
    """Códigos do catálogo (docs/GoblinShiftPlan.md, 1.6) usados pelo CSV Janitor."""

    # Arquivo que não é UTF-8.
    UNSUPPORTED_FORMAT = "UNSUPPORTED_FORMAT"
    # Aspas ambíguas, número irregular de campos, cabeçalho inválido, coluna inexistente.
    CSV_STRUCTURE_INVALID = "CSV_STRUCTURE_INVALID"
    # Qualquer limite de 1.7, na entrada ou na saída: tabela, registros, colunas, registro, campo.
    FIELD_LIMIT_EXCEEDED = "FIELD_LIMIT_EXCEEDED"


class TableRejectionError(Exception):
    """Interrompe a limpeza; `clean_table` a converte em `TableRejected`."""

    def __init__(self, code: RejectionCode, reason: str) -> None:
        super().__init__(f"{code}: {reason}")
        self.code = code
        self.reason = reason


@dataclass(frozen=True)
class TableRejected:
    code: RejectionCode
    reason: str


@dataclass(frozen=True)
class Counts:
    """Contagens de registros lógicos; cabeçalho e linhas vazias ignoradas ficam fora delas.

    Fecha sempre: `records_read == filtered_out + duplicates_removed + written`.
    """

    records_read: int
    filtered_out: int
    duplicates_removed: int
    written: int
    blank_lines_ignored: int


class WarningCode(StrEnum):
    ALL_RECORDS_FILTERED = "CSV_ALL_RECORDS_FILTERED"
    FORMULA_CELLS = "CSV_FORMULA_CELLS"


@dataclass(frozen=True)
class TableWarning:
    code: WarningCode
    detail: str


@dataclass(frozen=True)
class TableCleaned:
    content: bytes
    counts: Counts
    # Células de saída que uma planilha pode interpretar como fórmula, por coluna de saída.
    formula_cells: Mapping[str, int]
    warnings: tuple[TableWarning, ...]
