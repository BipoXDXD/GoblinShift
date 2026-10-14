"""`POST /auth/logout` e `POST /auth/logout-all` (docs/GoblinShiftPlan.md, 3.6; Frente B).

Fora de `/api/v1` e do OpenAPI. CSRF exigido nas duas; `logout` sem sessão válida também
responde 204, porque o estado final é o mesmo. As duas apagam os cookies e mandam o navegador
limpar os dados da origem (`Clear-Site-Data`).
"""

from fastapi import APIRouter, Request, Response, status

from goblinshift.entrypoints.api.cookies import CLEAR_SITE_DATA, clear_session_cookies
from goblinshift.entrypoints.api.dependencies import (
    DbSession,
    Now,
    OptionalSession,
    SessionPrincipal,
    client_ip,
)
from goblinshift.identity import (
    AuditEventType,
    AuditOutcome,
    record_audit_event,
    revoke_all_credentials,
    revoke_session,
)

router = APIRouter(prefix="/auth", include_in_schema=False)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(principal: OptionalSession, request: Request, session: DbSession, now: Now) -> Response:
    if principal is not None and principal.session_id is not None:
        with session.begin():
            revoke_session(session, principal.session_id, now)
            record_audit_event(
                session,
                event=AuditEventType.LOGOUT,
                user_id=principal.owner_id,
                ip=client_ip(request),
                outcome=AuditOutcome.SUCCESS,
                now=now,
            )
    return _signed_out()


@router.post("/logout-all", status_code=status.HTTP_204_NO_CONTENT)
def logout_all(
    principal: SessionPrincipal, request: Request, session: DbSession, now: Now
) -> Response:
    """Revoga todas as sessões e todas as chaves de API da conta."""
    with session.begin():
        revoke_all_credentials(session, principal.owner_id, now)
        record_audit_event(
            session,
            event=AuditEventType.LOGOUT_ALL,
            user_id=principal.owner_id,
            ip=client_ip(request),
            outcome=AuditOutcome.SUCCESS,
            now=now,
        )
    return _signed_out()


def _signed_out() -> Response:
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.headers["Clear-Site-Data"] = CLEAR_SITE_DATA
    clear_session_cookies(response)
    return response
