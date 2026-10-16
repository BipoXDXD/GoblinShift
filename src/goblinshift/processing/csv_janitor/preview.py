"""Amostra da tabela limpa para a interface (docs/GoblinShiftPlan.md, 2.2 e 3.6).

Cabeçalho e até 100 registros, sem passar de 64 KiB, sempre com vírgula, para a SPA ler um
formato só. Todo valor que uma planilha leria como fórmula sai com o prefixo tab, qualquer que
seja o `formula_guard` pedido: a amostra é para olhar, não para abrir como dado. O valor que já
começa com tab não ganha um segundo.
"""

from collections.abc import Sequence

from goblinshift.processing.csv_janitor.janitor import FORMULA_GUARD_PREFIX, looks_like_formula
from goblinshift.processing.csv_janitor.limits import Limits
from goblinshift.processing.csv_janitor.params import Delimiter
from goblinshift.processing.csv_janitor.reading import read_table
from goblinshift.processing.csv_janitor.writing import serialize_record

PREVIEW_MAX_RECORDS = 100
PREVIEW_MAX_BYTES = 64 * 1024
_PREVIEW_DELIMITER = ","


def table_preview(content: bytes, delimiter: Delimiter, limits: Limits) -> bytes | None:
    """`content` é a saída já limpa; `None` quando nem o cabeçalho cabe no teto."""
    parsed = read_table(content, delimiter, limits)
    lines = [_line(parsed.header)]
    size = len(lines[0])
    if size > PREVIEW_MAX_BYTES:
        return None
    for record in parsed.records[:PREVIEW_MAX_RECORDS]:
        line = _line(record)
        if size + len(line) > PREVIEW_MAX_BYTES:
            break
        lines.append(line)
        size += len(line)
    return b"".join(lines)


def _line(fields: Sequence[str]) -> bytes:
    return serialize_record([_escaped(value) for value in fields], _PREVIEW_DELIMITER).encode()


def _escaped(value: str) -> str:
    if value.startswith(FORMULA_GUARD_PREFIX) or not looks_like_formula(value):
        return value
    return FORMULA_GUARD_PREFIX + value
