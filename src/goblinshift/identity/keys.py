"""Formato, geração e verificação de chaves de API, sem banco (docs/GoblinShiftPlan.md, 3.6).

Formato do segredo entregue ao usuário (64 caracteres ASCII):

    gsk_<public_id>_<secret>

- `gsk_`: prefixo fixo, para que ferramentas de secret scanning reconheçam um vazamento;
- `public_id`: 16 dígitos hexadecimais minúsculos (64 bits aleatórios), guardado em claro e
  usado para localizar a chave antes de comparar o hash;
- `secret`: 32 bytes (256 bits) aleatórios em base64url sem padding, 43 caracteres.

O banco guarda só o SHA-256 do texto de `secret`. Hash rápido basta porque o segredo tem 256
bits de entropia: bcrypt e argon2 existem para senhas fracas. O hash é do texto, e não dos
bytes decodificados, para que variantes não canônicas do base64 não sejam aceitas.
"""

import base64
import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta

KEY_PREFIX = "gsk_"
PUBLIC_ID_BYTES = 8
SECRET_BYTES = 32
MIN_KEY_DAYS = 1
DEFAULT_KEY_DAYS = 30
MAX_KEY_DAYS = 90

# fullmatch e classes explícitas: `\d` aceitaria dígitos Unicode e `$` aceitaria "\n" no fim.
_KEY_PATTERN = re.compile(r"gsk_(?P<public_id>[0-9a-f]{16})_(?P<secret>[A-Za-z0-9_-]{43})")
_KEY_LENGTH = 64


@dataclass(frozen=True)
class GeneratedKey:
    """Chave recém-criada. `token` é exibido uma única vez e nunca é persistido."""

    public_id: str
    token: str = field(repr=False)
    secret_hash: bytes = field(repr=False)


@dataclass(frozen=True)
class PresentedKey:
    """Chave recebida numa requisição, já separada em identificador e segredo."""

    public_id: str
    secret: str = field(repr=False)


class InvalidKeyLifetimeError(ValueError):
    pass


def generate_key() -> GeneratedKey:
    public_id = secrets.token_hex(PUBLIC_ID_BYTES)
    secret = base64.urlsafe_b64encode(secrets.token_bytes(SECRET_BYTES)).rstrip(b"=").decode()
    return GeneratedKey(
        public_id=public_id,
        token=f"{KEY_PREFIX}{public_id}_{secret}",
        secret_hash=_hash_secret(secret),
    )


def parse_key(token: str) -> PresentedKey | None:
    """Separa a chave nas suas partes; qualquer desvio do formato devolve None."""
    # O tamanho é conferido antes da regex para que entrada enorme custe O(1).
    if len(token) != _KEY_LENGTH:
        return None
    match = _KEY_PATTERN.fullmatch(token)
    if match is None:
        return None
    return PresentedKey(public_id=match["public_id"], secret=match["secret"])


def secret_matches(presented: PresentedKey, stored_hash: bytes) -> bool:
    # Tempo constante: a comparação não revela quantos bytes do hash coincidem.
    return hmac.compare_digest(_hash_secret(presented.secret), stored_hash)


def key_lifetime(days: int) -> timedelta:
    if not MIN_KEY_DAYS <= days <= MAX_KEY_DAYS:
        raise InvalidKeyLifetimeError(
            f"validade de {days} dias fora da faixa de {MIN_KEY_DAYS} a {MAX_KEY_DAYS}"
        )
    return timedelta(days=days)


def key_is_active(*, expires_at: datetime, revoked_at: datetime | None, now: datetime) -> bool:
    """A validade é o intervalo semiaberto [criação, expiração): no instante exato já expirou."""
    return revoked_at is None and now < expires_at


def _hash_secret(secret: str) -> bytes:
    return hashlib.sha256(secret.encode("ascii")).digest()
