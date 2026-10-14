"""Serialização da saída (docs/GoblinShiftPlan.md, 1.3): RFC 4180, CRLF e aspas só se preciso.

Feita aqui, e não com `csv.writer`, porque o `formula_guard` precisa do prefixo tab *dentro* de
aspas, como pede a OWASP, e o QUOTE_MINIMAL do módulo não põe aspas num campo só por começar com
tab. A leitura continua com o `csv`, e um teste de propriedade confere a ida e volta entre os dois.
"""

from collections.abc import Sequence

LINE_TERMINATOR = "\r\n"
_QUOTE = '"'


def serialize_record(fields: Sequence[str], delimiter: str) -> str:
    if list(fields) == [""]:
        # Sem aspas, o registro de um único campo vazio viraria uma linha vazia.
        return _QUOTE * 2 + LINE_TERMINATOR
    return delimiter.join(_quote_if_needed(value, delimiter) for value in fields) + LINE_TERMINATOR


def _quote_if_needed(value: str, delimiter: str) -> str:
    if value.startswith("\t") or any(char in value for char in (delimiter, _QUOTE, "\r", "\n")):
        return _QUOTE + value.replace(_QUOTE, _QUOTE * 2) + _QUOTE
    return value
