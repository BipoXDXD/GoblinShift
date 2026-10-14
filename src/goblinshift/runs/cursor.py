"""Cursor opaco da listagem de execuções (docs/GoblinShiftPlan.md, 1.6 e 3.3).

O cursor carrega a posição de keyset `(created_at, id)`, os filtros e a direção da consulta que o
gerou, e vale 24 horas. É assinado com HMAC-SHA256 para que o cliente não forje posições nem troque
filtros sem a API perceber; não é cifrado, porque só contém o que o próprio dono já viu.

Formato: `base64url(payload JSON) "." base64url(HMAC)`, sem padding.
"""

import base64
import binascii
import hashlib
import hmac
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

CURSOR_TTL = timedelta(hours=24)
# Só existe a ordem "mais recentes primeiro"; a direção entra no cursor para que uma ordem nova
# no futuro não aceite cursores emitidos para a antiga.
DIRECTION_DESC = "desc"
_FORMAT_VERSION = 1
_SEPARATOR = "."


class InvalidCursorError(ValueError):
    """Cursor malformado, adulterado, vencido ou de outra consulta (→ 400)."""


class CursorScopeMismatchError(InvalidCursorError):
    """O cursor foi emitido para outros filtros ou outra direção."""


@dataclass(frozen=True)
class Position:
    """Último item entregue; a próxima página começa estritamente depois dele."""

    created_at: datetime
    run_id: UUID


def encode_cursor(
    position: Position, scope: Mapping[str, str | None], *, secret: bytes, now: datetime
) -> str:
    payload = {
        "v": _FORMAT_VERSION,
        "d": DIRECTION_DESC,
        "f": dict(scope),
        "t": position.created_at.isoformat(),
        "i": str(position.run_id),
        "x": int((now + CURSOR_TTL).timestamp()),
    }
    body = _b64encode(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())
    return f"{body}{_SEPARATOR}{_b64encode(_sign(body, secret))}"


def decode_cursor(
    token: str, scope: Mapping[str, str | None], *, secret: bytes, now: datetime
) -> Position:
    """Confere assinatura, validade e escopo antes de confiar em qualquer campo do cursor."""
    body, separator, signature = token.partition(_SEPARATOR)
    if not separator or not body.isascii() or not body or not signature:
        raise InvalidCursorError("cursor malformado")
    if not hmac.compare_digest(_b64decode(signature), _sign(body, secret)):
        raise InvalidCursorError("assinatura do cursor não confere")
    payload = _parse_payload(_b64decode(body))
    if now.timestamp() > payload.expires_at:
        raise InvalidCursorError("cursor vencido")
    if payload.direction != DIRECTION_DESC or payload.scope != dict(scope):
        raise CursorScopeMismatchError("cursor emitido para outros filtros")
    return payload.position


@dataclass(frozen=True)
class _Payload:
    direction: object
    scope: object
    expires_at: int
    position: Position


def _parse_payload(raw: bytes) -> _Payload:
    """Só roda depois da assinatura conferida; ainda assim não presume tipos."""
    try:
        payload = json.loads(raw)
    except ValueError as error:
        raise InvalidCursorError("payload do cursor inválido") from error
    if not isinstance(payload, dict) or payload.get("v") != _FORMAT_VERSION:
        raise InvalidCursorError("versão de cursor desconhecida")
    expires_at, created_at, run_id = payload.get("x"), payload.get("t"), payload.get("i")
    if not isinstance(expires_at, int) or not isinstance(created_at, str):
        raise InvalidCursorError("payload do cursor inválido")
    if not isinstance(run_id, str):
        raise InvalidCursorError("payload do cursor inválido")
    try:
        position = Position(datetime.fromisoformat(created_at), UUID(run_id))
    except ValueError as error:
        raise InvalidCursorError("posição do cursor inválida") from error
    return _Payload(payload.get("d"), payload.get("f"), expires_at, position)


def _sign(body: str, secret: bytes) -> bytes:
    return hmac.new(secret, body.encode("ascii"), hashlib.sha256).digest()


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode(text: str) -> bytes:
    try:
        return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except (binascii.Error, ValueError) as error:
        raise InvalidCursorError("cursor malformado") from error
