"""`DELETE /me`: exclusão da conta e de todos os seus dados (1.6 e 1.8; Frente C).

Só sessão com autenticação recente (`RecentSessionPrincipal`) e CSRF; responde 202 com
`Clear-Site-Data`. `GET /me` fica em `me.py` (Frente D).

1. Uma transação marca `deletion_requested_at`, revoga sessões e chaves e audita o pedido: a
   partir do commit, nenhuma credencial da conta autentica.
2. Outra cancela a execução não terminal, se houver.
3. A manutenção purga os dados em lotes em até 24 h.

Repetir é seguro: responde 202 de novo, mantém o primeiro pedido e completa o que faltou.
"""

from fastapi import APIRouter, Request, Response
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.entrypoints.api.dependencies import DbSession, Now, RecentSessionPrincipal, Storage
from goblinshift.entrypoints.api.problems import ApiProblem, ProblemCode
from goblinshift.identity import (
    AuditEventType,
    AuditOutcome,
    UnknownAccountError,
    record_audit_event,
    request_account_deletion,
)
from goblinshift.runs.purge import cancel_active_run

router = APIRouter(tags=["me"])

# Apaga cookies e armazenamento local do site no navegador (o servidor já revogou a sessão).
CLEAR_SITE_DATA = '"cookies", "storage"'


@router.delete("/me", operation_id="deleteMe", status_code=202)
def delete_me(
    principal: RecentSessionPrincipal,
    session: DbSession,
    storage: Storage,
    now: Now,
    request: Request,
) -> Response:
    try:
        with session.begin():
            request_account_deletion(session, principal.owner_id, now)
            record_audit_event(
                session,
                event=AuditEventType.ACCOUNT_DELETION_REQUESTED,
                user_id=principal.owner_id,
                ip=request.client.host if request.client else None,
                outcome=AuditOutcome.SUCCESS,
                now=now,
            )
    except UnknownAccountError:
        raise ApiProblem(
            ProblemCode.AUTHENTICATION_REQUIRED, "A sessão não vale mais para esta conta."
        ) from None
    # O cancelamento abre as próprias transações curtas; por isso recebe a fábrica de Sessions.
    factory: sessionmaker[Session] = request.app.state.session_factory
    cancel_active_run(factory, storage, principal.owner_id)
    return Response(status_code=202, headers={"Clear-Site-Data": CLEAR_SITE_DATA})
