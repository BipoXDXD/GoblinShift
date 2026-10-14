"""Provedores de login: endereços fixos e normalização das identidades (ADR 008; 3.6).

A identidade é `(issuer, subject)`: `https://accounts.google.com` + `sub` no Google e
`https://github.com` + o `id` numérico no GitHub, nunca o `login`, que pode mudar. O que vem do
provedor é entrada de terceiro: passa por um modelo estrito antes de virar `ExternalIdentity`.

Os endereços e o algoritmo do ID token ficam no código, sem discovery: um documento de discovery
adulterado não consegue trocar o endpoint de token, a JWKS nem a lista de algoritmos aceitos.
Este módulo não fala HTTP; o fluxo OAuth (Authlib) mora no entrypoint `auth_login`.
"""

from collections.abc import Mapping
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from goblinshift.identity.users import ExternalIdentity


class ProviderName(StrEnum):
    GOOGLE = "google"
    GITHUB = "github"


GOOGLE_ISSUER = "https://accounts.google.com"
# O Google documenta os dois valores de `iss`; a conta é gravada sempre com o primeiro.
GOOGLE_ACCEPTED_ISSUERS = (GOOGLE_ISSUER, "accounts.google.com")
GOOGLE_AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105 (endereço, não segredo)
GOOGLE_JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
GOOGLE_ID_TOKEN_ALGORITHMS = ("RS256",)
# Só o escopo `openid` (1.8, minimização): o ID token não traz nome, então o nome é fixo (D-8).
GOOGLE_SCOPE = "openid"
GOOGLE_DISPLAY_NAME = "Conta Google"

GITHUB_ISSUER = "https://github.com"
GITHUB_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"  # noqa: S105 (endereço)
GITHUB_USER_URL = "https://api.github.com/user"
GITHUB_ACCEPT = "application/vnd.github+json"
# O `GET /user` tem poucos KiB; um corpo maior é lixo ou ataque e nem chega ao parser.
MAX_GITHUB_USER_BYTES = 64 * 1024

# `sub` do OIDC: até 255 caracteres ASCII visíveis (OpenID Connect Core, 2).
_OIDC_SUBJECT = r"^[\x21-\x7e]{1,255}$"
# Regras de nome de usuário do GitHub: letras, dígitos e hífen, até 39 caracteres.
_GITHUB_LOGIN = r"^[A-Za-z0-9-]{1,39}$"
_MAX_BIGINT = 2**63 - 1


class InvalidProviderIdentityError(ValueError):
    """O provedor devolveu uma identidade que não passa no modelo estrito."""


class _GoogleClaims(BaseModel):
    # Os demais claims (`aud`, `exp`, `nonce`...) já foram validados pelo Authlib.
    model_config = ConfigDict(strict=True, extra="ignore")

    iss: Literal["https://accounts.google.com", "accounts.google.com"]
    sub: Annotated[str, Field(pattern=_OIDC_SUBJECT)]


class _GitHubUser(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore")

    id: Annotated[int, Field(gt=0, le=_MAX_BIGINT)]
    login: Annotated[str, Field(pattern=_GITHUB_LOGIN)]


def google_identity(claims: Mapping[str, object]) -> ExternalIdentity:
    """Identidade a partir dos claims de um ID token já validado (assinatura, `aud`, `exp`)."""
    try:
        parsed = _GoogleClaims.model_validate(dict(claims))
    except ValidationError as exc:
        raise InvalidProviderIdentityError("claims do Google fora do modelo") from exc
    return ExternalIdentity(
        issuer=GOOGLE_ISSUER, subject=parsed.sub, display_name=GOOGLE_DISPLAY_NAME
    )


def github_identity(body: bytes) -> ExternalIdentity:
    """Identidade a partir do corpo de `GET https://api.github.com/user`."""
    if len(body) > MAX_GITHUB_USER_BYTES:
        raise InvalidProviderIdentityError("resposta do GitHub grande demais")
    try:
        user = _GitHubUser.model_validate_json(body)
    except ValidationError as exc:
        raise InvalidProviderIdentityError("usuário do GitHub fora do modelo") from exc
    return ExternalIdentity(issuer=GITHUB_ISSUER, subject=str(user.id), display_name=user.login)
