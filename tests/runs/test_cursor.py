"""Cursor opaco e assinado da listagem de execuções (docs/GoblinShiftPlan.md, 1.6)."""

import base64
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from goblinshift.runs.cursor import (
    CursorScopeMismatchError,
    InvalidCursorError,
    Position,
    decode_cursor,
    encode_cursor,
)

SECRET = b"segredo-de-teste-com-32-bytes-ok"
NOW = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)
POSITION = Position(
    created_at=datetime(2026, 10, 13, 11, 59, 30, 123456, tzinfo=UTC),
    run_id=UUID("01900000-0000-7000-8000-000000000001"),
)
SCOPE: dict[str, str | None] = {"recipe_id": "csv-janitor", "status": None}


def test_cursor_devolve_a_posicao_codificada() -> None:
    token = encode_cursor(POSITION, SCOPE, secret=SECRET, now=NOW)

    assert decode_cursor(token, SCOPE, secret=SECRET, now=NOW) == POSITION


def test_cursor_vale_ate_24_horas() -> None:
    token = encode_cursor(POSITION, SCOPE, secret=SECRET, now=NOW)
    last_moment = NOW + timedelta(hours=24)

    assert decode_cursor(token, SCOPE, secret=SECRET, now=last_moment) == POSITION


def test_cursor_vencido_e_recusado() -> None:
    token = encode_cursor(POSITION, SCOPE, secret=SECRET, now=NOW)

    with pytest.raises(InvalidCursorError):
        decode_cursor(token, SCOPE, secret=SECRET, now=NOW + timedelta(hours=24, seconds=1))


def test_cursor_com_filtros_diferentes_e_recusado() -> None:
    token = encode_cursor(POSITION, SCOPE, secret=SECRET, now=NOW)
    other_scope = {**SCOPE, "status": "queued"}

    with pytest.raises(CursorScopeMismatchError):
        decode_cursor(token, other_scope, secret=SECRET, now=NOW)


def test_cursor_assinado_com_outro_segredo_e_recusado() -> None:
    token = encode_cursor(POSITION, SCOPE, secret=SECRET, now=NOW)

    with pytest.raises(InvalidCursorError):
        decode_cursor(token, SCOPE, secret=b"outro-segredo-de-32-bytes-......", now=NOW)


def test_cursor_com_payload_adulterado_e_recusado() -> None:
    token = encode_cursor(POSITION, SCOPE, secret=SECRET, now=NOW)
    payload_b64, signature = token.split(".")
    payload = json.loads(base64.urlsafe_b64decode(payload_b64 + "=" * (-len(payload_b64) % 4)))
    payload["i"] = "01900000-0000-7000-8000-000000000099"
    forged = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")

    with pytest.raises(InvalidCursorError):
        decode_cursor(f"{forged}.{signature}", SCOPE, secret=SECRET, now=NOW)


@pytest.mark.parametrize(
    "token",
    ["", ".", "abc", "abc.def", "a.b.c", "!!!.???", "\x00.\x00", "é.é"],
)
def test_cursor_malformado_e_recusado(token: str) -> None:
    with pytest.raises(InvalidCursorError):
        decode_cursor(token, SCOPE, secret=SECRET, now=NOW)


def test_divergencia_de_filtros_tambem_e_cursor_invalido() -> None:
    # A rota responde 400 aos dois casos; a subclasse só muda a mensagem.
    assert issubclass(CursorScopeMismatchError, InvalidCursorError)
