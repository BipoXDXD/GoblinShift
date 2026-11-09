"""Cursor opaco e assinado da listagem de execuções (docs/GoblinShiftPlan.md, 1.6)."""

import base64
import hashlib
import hmac
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


# --- Payload de assinatura válida: o cursor continua sendo dado externo --------------------------

VALID_PAYLOAD = {
    "v": 1,
    "d": "desc",
    "f": SCOPE,
    "t": "2026-10-13T11:59:30.123456+00:00",
    "i": "01900000-0000-7000-8000-000000000001",
    "x": 1791979200,
}


def b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def signed(raw: bytes) -> str:
    """Token com assinatura correta, como o de um segredo vazado ou de um formato antigo."""
    body = b64url(raw)
    return f"{body}.{b64url(hmac.new(SECRET, body.encode(), hashlib.sha256).digest())}"


def signed_payload(**changes: object) -> str:
    return signed(json.dumps({**VALID_PAYLOAD, **changes}).encode())


def without(key: str) -> bytes:
    return json.dumps({k: v for k, v in VALID_PAYLOAD.items() if k != key}).encode()


def test_payload_assinado_a_mao_e_aceito_quando_bem_formado() -> None:
    # Garante que os casos abaixo caem na regra do payload, não na conferência da assinatura.
    assert decode_cursor(signed_payload(), SCOPE, secret=SECRET, now=NOW) == POSITION


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        pytest.param(b"nao e json", "payload do cursor inválido", id="nao-json"),
        pytest.param(b"\xff", "payload do cursor inválido", id="bytes-invalidos"),
        pytest.param(b"[]", "versão de cursor desconhecida", id="lista"),
        pytest.param(without("v"), "versão de cursor desconhecida", id="sem-versao"),
        pytest.param(without("x"), "payload do cursor inválido", id="sem-validade"),
        pytest.param(without("t"), "payload do cursor inválido", id="sem-data"),
        pytest.param(without("i"), "payload do cursor inválido", id="sem-id"),
    ],
)
def test_payload_assinado_fora_do_formato_e_recusado(raw: bytes, message: str) -> None:
    with pytest.raises(InvalidCursorError, match=message):
        decode_cursor(signed(raw), SCOPE, secret=SECRET, now=NOW)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        pytest.param({"v": 2}, "versão de cursor desconhecida", id="versao-futura"),
        pytest.param({"x": "amanha"}, "payload do cursor inválido", id="validade-texto"),
        pytest.param({"t": 1791979200}, "payload do cursor inválido", id="data-numero"),
        pytest.param({"i": 1}, "payload do cursor inválido", id="id-numero"),
        pytest.param({"t": "ontem"}, "posição do cursor inválida", id="data-ruim"),
        pytest.param({"i": "abc"}, "posição do cursor inválida", id="id-ruim"),
    ],
)
def test_campo_de_tipo_errado_no_payload_assinado_e_recusado(
    changes: dict[str, object], message: str
) -> None:
    with pytest.raises(InvalidCursorError, match=message):
        decode_cursor(signed_payload(**changes), SCOPE, secret=SECRET, now=NOW)


def test_cursor_de_outra_direcao_com_os_mesmos_filtros_e_recusado() -> None:
    with pytest.raises(CursorScopeMismatchError):
        decode_cursor(signed_payload(d="asc"), SCOPE, secret=SECRET, now=NOW)


def test_validade_em_ponto_flutuante_no_payload_assinado_e_recusada() -> None:
    with pytest.raises(InvalidCursorError, match="payload do cursor inválido"):
        decode_cursor(signed_payload(x=1791979200.0), SCOPE, secret=SECRET, now=NOW)


@pytest.mark.parametrize(
    "version",
    [pytest.param(True, id="booleano"), pytest.param(1.0, id="ponto-flutuante")],
)
def test_versao_igual_a_1_so_por_coercao_e_recusada(version: object) -> None:
    with pytest.raises(InvalidCursorError):
        decode_cursor(signed_payload(v=version), SCOPE, secret=SECRET, now=NOW)


def test_data_sem_fuso_no_payload_assinado_e_recusada() -> None:
    with pytest.raises(InvalidCursorError):
        decode_cursor(signed_payload(t="2026-10-13T11:59:30.123456"), SCOPE, secret=SECRET, now=NOW)


def test_data_com_outro_fuso_conserva_o_instante() -> None:
    token = signed_payload(t="2026-10-13T08:59:30.123456-03:00")

    assert decode_cursor(token, SCOPE, secret=SECRET, now=NOW) == POSITION
