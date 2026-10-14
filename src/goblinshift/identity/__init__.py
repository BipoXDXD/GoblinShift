"""Login, sessões, CSRF, chaves de API e escopos (generic).

Ver docs/GoblinShiftPlan.md, seções 3.2 e 3.6. Na fatia 3 só existem contas criadas pelo
administrador e chaves de API. O módulo não conhece HTTP: o entrypoint lê o header
`Authorization` e chama `authenticate_api_key`.
"""

from goblinshift.identity.accounts import (
    ADMIN_ISSUER,
    Account,
    InvalidDisplayNameError,
    IssuedKey,
    UnknownAccountError,
    authenticate_api_key,
    create_admin_user,
    find_account,
    issue_key,
    revoke_key,
)
from goblinshift.identity.keys import (
    DEFAULT_KEY_DAYS,
    KEY_PREFIX,
    MAX_KEY_DAYS,
    GeneratedKey,
    InvalidKeyLifetimeError,
    PresentedKey,
    generate_key,
    key_is_active,
    key_lifetime,
    parse_key,
    secret_matches,
)
from goblinshift.identity.principal import InvalidScopesError, Principal, Scope, parse_scopes

__all__ = [
    "ADMIN_ISSUER",
    "DEFAULT_KEY_DAYS",
    "KEY_PREFIX",
    "MAX_KEY_DAYS",
    "Account",
    "GeneratedKey",
    "InvalidDisplayNameError",
    "InvalidKeyLifetimeError",
    "InvalidScopesError",
    "IssuedKey",
    "PresentedKey",
    "Principal",
    "Scope",
    "UnknownAccountError",
    "authenticate_api_key",
    "create_admin_user",
    "find_account",
    "generate_key",
    "issue_key",
    "key_is_active",
    "key_lifetime",
    "parse_key",
    "parse_scopes",
    "revoke_key",
    "secret_matches",
]
