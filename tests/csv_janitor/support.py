"""Atalhos dos testes do CSV Janitor: tabelas escritas como texto, resultado já conferido."""

from goblinshift.processing.csv_janitor import (
    Limits,
    Params,
    TableCleaned,
    TableRejected,
    clean_table,
)


def cleaned(
    table: str | bytes, params: Params | None = None, limits: Limits | None = None
) -> TableCleaned:
    result = clean_table(_as_bytes(table), params or Params(), limits)
    assert isinstance(result, TableCleaned), result
    return result


def rejected(
    table: str | bytes, params: Params | None = None, limits: Limits | None = None
) -> TableRejected:
    result = clean_table(_as_bytes(table), params or Params(), limits)
    assert isinstance(result, TableRejected), result
    return result


def output(table: str | bytes, params: Params | None = None) -> str:
    return cleaned(table, params).content.decode()


def _as_bytes(table: str | bytes) -> bytes:
    return table.encode() if isinstance(table, str) else table
