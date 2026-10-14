"""Login, sessões, CSRF, chaves de API, escopos, estado da conta, auditoria e limite de
requisições por conta (generic).

Ver docs/GoblinShiftPlan.md, seções 3.2 e 3.6. O módulo não conhece HTTP: os entrypoints leem
cookies e headers e chamam estas funções. Exportações fixadas na Fase 0 da fatia 6, para que as
frentes paralelas não precisem editar este arquivo.
"""

from goblinshift.identity.accounts import (
    ADMIN_ISSUER,
    MAX_ACTIVE_KEYS,
    Account,
    ActiveKeyLimitError,
    InvalidDisplayNameError,
    IssuedKey,
    KeySummary,
    UnknownAccountError,
    authenticate_api_key,
    block_account,
    create_admin_user,
    find_account,
    issue_key,
    issue_key_for_session,
    list_keys,
    request_account_deletion,
    revoke_key,
    revoke_owned_key,
    unblock_account,
)
from goblinshift.identity.audit import AuditEventType, AuditOutcome, truncate_ip
from goblinshift.identity.audit import record as record_audit_event
from goblinshift.identity.csrf import cross_site_request, csrf_token_matches, issue_csrf_token
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
from goblinshift.identity.purge import purge_owner
from goblinshift.identity.rate_limit import (
    REQUESTS_PER_MINUTE,
    RequestClass,
    RequestLimitExceededError,
    count_request,
)
from goblinshift.identity.sessions import (
    RECENT_AUTH_WINDOW,
    IssuedSession,
    authenticate_session,
    revoke_all_credentials,
    revoke_session,
    start_session,
)
from goblinshift.identity.users import ExternalIdentity, LoginRefused, resolve_login

__all__ = [
    "ADMIN_ISSUER",
    "DEFAULT_KEY_DAYS",
    "KEY_PREFIX",
    "MAX_ACTIVE_KEYS",
    "MAX_KEY_DAYS",
    "RECENT_AUTH_WINDOW",
    "REQUESTS_PER_MINUTE",
    "Account",
    "ActiveKeyLimitError",
    "AuditEventType",
    "AuditOutcome",
    "ExternalIdentity",
    "GeneratedKey",
    "InvalidDisplayNameError",
    "InvalidKeyLifetimeError",
    "InvalidScopesError",
    "IssuedKey",
    "IssuedSession",
    "KeySummary",
    "LoginRefused",
    "PresentedKey",
    "Principal",
    "RequestClass",
    "RequestLimitExceededError",
    "Scope",
    "UnknownAccountError",
    "authenticate_api_key",
    "authenticate_session",
    "block_account",
    "count_request",
    "create_admin_user",
    "cross_site_request",
    "csrf_token_matches",
    "find_account",
    "generate_key",
    "issue_csrf_token",
    "issue_key",
    "issue_key_for_session",
    "key_is_active",
    "key_lifetime",
    "list_keys",
    "parse_key",
    "parse_scopes",
    "purge_owner",
    "record_audit_event",
    "request_account_deletion",
    "resolve_login",
    "revoke_all_credentials",
    "revoke_key",
    "revoke_owned_key",
    "revoke_session",
    "secret_matches",
    "start_session",
    "truncate_ip",
    "unblock_account",
]
