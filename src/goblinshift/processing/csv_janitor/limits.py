"""Limites de uma tabela (docs/GoblinShiftPlan.md, 1.7), contados em bytes UTF-8.

Valem na entrada e na saída. Exceder qualquer um rejeita a tabela inteira.
"""

from dataclasses import dataclass

KIB = 1024
MIB = 1024 * KIB


@dataclass(frozen=True)
class Limits:
    table_bytes: int = 8 * MIB
    records: int = 20_000
    columns: int = 100
    # Registro lógico: as linhas físicas que o compõem, com aspas, delimitadores e terminadores.
    record_bytes: int = 1 * MIB
    # Valor do campo, sem as aspas do CSV.
    field_bytes: int = 64 * KIB
