"""Leitura da estrutura de uma tabela (docs/GoblinShiftPlan.md, 1.3 e 2.3).

O módulo `csv` trata aspas, delimitadores internos e quebras de linha dentro de campos, mas não
confere o número de campos de cada registro, conta o tamanho dos campos em caracteres (e num
estado global) e numera linhas físicas. Este módulo cobre essas lacunas.
"""

import csv
import io
from collections.abc import Iterator
from dataclasses import dataclass

from goblinshift.processing.csv_janitor.limits import Limits
from goblinshift.processing.csv_janitor.params import Delimiter
from goblinshift.processing.csv_janitor.results import RejectionCode, TableRejectionError


@dataclass(frozen=True)
class Table:
    header: tuple[str, ...]
    records: tuple[tuple[str, ...], ...]
    blank_lines_ignored: int


def read_table(data: bytes, delimiter: Delimiter, limits: Limits) -> Table:
    """Lê cabeçalho e registros, ou rejeita a tabela inteira com o primeiro problema encontrado."""
    if len(data) > limits.table_bytes:
        raise _limit_exceeded(f"a tabela tem {len(data)} bytes; o limite é {limits.table_bytes}")
    _let_record_limit_govern(limits)
    lines = _RecordMeter(io.StringIO(_decode(data), newline=""), limits.record_bytes)
    header: tuple[str, ...] | None = None
    records: list[tuple[str, ...]] = []
    blank_lines = 0
    try:
        for row in csv.reader(lines, delimiter=delimiter, strict=True):
            lines.start_next_record()
            if not row:
                # Numa tabela de uma coluna, linha vazia e registro com o único campo vazio são
                # indistinguíveis; descartar dados seria pior que preservá-los (1.3).
                if header is None or len(header) > 1:
                    blank_lines += 1
                    continue
                row = [""]
            position = _position(header, len(records))
            _check_field_sizes(row, position, limits)
            if header is None:
                header = _parse_header(row, limits)
                continue
            if len(row) != len(header):
                raise _structure_invalid(
                    f"{position} tem {len(row)} campo(s); o cabeçalho tem {len(header)}"
                )
            if len(records) == limits.records:
                raise _limit_exceeded(f"a tabela passa de {limits.records} registros")
            records.append(tuple(row))
    except csv.Error as error:
        raise _structure_invalid(
            f"{_position(header, len(records))}: aspas deixam a fronteira do registro ambígua "
            f"({error})"
        ) from None
    except _RecordTooLargeError:
        raise _limit_exceeded(
            f"{_position(header, len(records))} passa de {limits.record_bytes} bytes"
        ) from None
    if header is None:
        raise _structure_invalid("a tabela não tem cabeçalho")
    return Table(header, tuple(records), blank_lines)


def _let_record_limit_govern(limits: Limits) -> None:
    # `field_size_limit` conta caracteres e é global ao módulo csv. O medidor de registro barra
    # qualquer registro acima de `record_bytes` antes de o csv recebê-lo, e um campo nunca tem
    # mais caracteres que o registro tem bytes; com este piso, quem rejeita é sempre o medidor,
    # com o código e o motivo certos.
    if csv.field_size_limit() < limits.record_bytes:
        csv.field_size_limit(limits.record_bytes)


def _decode(data: bytes) -> str:
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise TableRejectionError(
            RejectionCode.UNSUPPORTED_FORMAT, f"não é UTF-8 válido (byte {error.start})"
        ) from None


class _RecordTooLargeError(Exception):
    pass


class _RecordMeter:
    """Entrega as linhas físicas ao csv e soma os bytes UTF-8 do registro em andamento."""

    def __init__(self, lines: Iterator[str], limit: int) -> None:
        self._lines = lines
        self._limit = limit
        self._pending = 0

    def __iter__(self) -> _RecordMeter:
        return self

    def __next__(self) -> str:
        line = next(self._lines)
        self._pending += len(line.encode())
        if self._pending > self._limit:
            raise _RecordTooLargeError
        return line

    def start_next_record(self) -> None:
        # O reader só pede uma linha nova quando precisa dela, então, a cada registro entregue,
        # as linhas medidas até aqui são exatamente as daquele registro.
        self._pending = 0


def _parse_header(row: list[str], limits: Limits) -> tuple[str, ...]:
    if len(row) > limits.columns:
        raise _limit_exceeded(f"o cabeçalho tem {len(row)} colunas; o limite é {limits.columns}")
    if "" in row:
        raise _structure_invalid(f"o cabeçalho tem coluna sem nome (coluna {row.index('') + 1})")
    repeated = sorted({name for name in row if row.count(name) > 1})
    if repeated:
        raise _structure_invalid(f"o cabeçalho repete colunas: {repeated}")
    return tuple(row)


def _check_field_sizes(row: list[str], position: str, limits: Limits) -> None:
    for column, value in enumerate(row, start=1):
        size = len(value.encode())
        if size > limits.field_bytes:
            raise _limit_exceeded(
                f"{position}, campo {column}: {size} bytes; o limite é {limits.field_bytes}"
            )


def _position(header: tuple[str, ...] | None, records_so_far: int) -> str:
    """Posição pelo índice do registro lógico (1 = primeiro após o cabeçalho), nunca pela linha."""
    return "o cabeçalho" if header is None else f"o registro {records_so_far + 1}"


def _structure_invalid(reason: str) -> TableRejectionError:
    return TableRejectionError(RejectionCode.CSV_STRUCTURE_INVALID, reason)


def _limit_exceeded(reason: str) -> TableRejectionError:
    return TableRejectionError(RejectionCode.FIELD_LIMIT_EXCEEDED, reason)
