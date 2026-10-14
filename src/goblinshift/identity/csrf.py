"""Verificação de CSRF, sem banco (docs/GoblinShiftPlan.md, 3.6; Frente B).

Nesta ordem, para toda mutação autenticada por cookie: Fetch Metadata (`Sec-Fetch-Site`
presente e diferente de `same-origin` recusa), `Origin` presente e diferente da origem pública
recusa, e o token *signed double-submit*: `X-CSRF-Token` igual ao cookie `__Host-gs_csrf`, cujo
valor é `aleatório ‖ HMAC-SHA256(chave, id da sessão ‖ aleatório)`, conferido em tempo
constante.

Formato do token (87 caracteres ASCII): `<aleatório>.<hmac>`, os dois em base64url sem padding
(32 bytes cada). O HMAC é calculado sobre o texto do aleatório, e não sobre os bytes
decodificados, para que variantes não canônicas do base64 não validem.
"""

import base64
import hashlib
import hmac
import re
import secrets
from uuid import UUID

RANDOM_BYTES = 32
SAME_ORIGIN = "same-origin"
_SEPARATOR = "."
# fullmatch e classes explícitas: `$` aceitaria "\n" no fim, e `\w`, letras Unicode.
_TOKEN_PATTERN = re.compile(r"(?P<random>[A-Za-z0-9_-]{43})\.(?P<mac>[A-Za-z0-9_-]{43})")
_TOKEN_LENGTH = 87


def issue_csrf_token(key: bytes, session_id: UUID) -> str:
    random_part = _b64(secrets.token_bytes(RANDOM_BYTES))
    return f"{random_part}{_SEPARATOR}{_mac(key, session_id, random_part)}"


def csrf_token_matches(
    key: bytes, session_id: UUID, cookie: str | None, header: str | None
) -> bool:
    """True só se o header repete o cookie e o HMAC do cookie é desta sessão.

    O formato é conferido antes de qualquer comparação: `hmac.compare_digest` só aceita texto
    ASCII, e entrada enorme custa O(1).
    """
    if cookie is None or header is None:
        return False
    if len(cookie) != _TOKEN_LENGTH or len(header) != _TOKEN_LENGTH:
        return False
    match = _TOKEN_PATTERN.fullmatch(cookie)
    if match is None or not header.isascii():
        return False
    same_value = hmac.compare_digest(cookie, header)
    signed_here = hmac.compare_digest(match["mac"], _mac(key, session_id, match["random"]))
    return same_value and signed_here


def cross_site_request(sec_fetch_site: str | None, origin: str | None, public_origin: str) -> bool:
    """True quando Fetch Metadata ou `Origin` mostram que a requisição veio de outra origem.

    Header ausente não prova nada (clientes que não são navegador não os enviam): a decisão
    final fica com o token. `Origin: null` vem de iframe com sandbox ou de redirect entre
    origens e é tratado como outra origem.
    """
    if sec_fetch_site is not None and sec_fetch_site != SAME_ORIGIN:
        return True
    return origin is not None and origin != public_origin


def _mac(key: bytes, session_id: UUID, random_part: str) -> str:
    digest = hmac.new(key, session_id.bytes + random_part.encode("ascii"), hashlib.sha256)
    return _b64(digest.digest())


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
