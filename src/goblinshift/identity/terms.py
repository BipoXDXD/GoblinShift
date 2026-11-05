"""Aceite versionado dos termos de uso e do aviso de privacidade (1.8; ADR 029).

A conta guarda a última versão aceita e a data do aceite (`app_user.terms_version` e
`terms_accepted_at`, migração 0006); cada aceite novo também vira evento de auditoria. A versão
vigente é uma constante do servidor, nunca um valor do cliente: trocar o texto em `docs/legal`
sobe esta constante, e toda conta precisa aceitar de novo.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from goblinshift.identity.accounts import UnknownAccountError
from goblinshift.identity.audit import AuditEventType, AuditOutcome
from goblinshift.identity.audit import record as record_audit_event
from goblinshift.identity.tables import app_user

# "rascunho" até a revisão jurídica (plano da fatia 8, F8-5). É a versão declarada no cabeçalho e
# na última linha do histórico dos dois textos de docs/legal (tests/test_legal_texts.py).
CURRENT_TERMS_VERSION = "v4-rascunho"


@dataclass(frozen=True)
class TermsAcceptance:
    """Resultado do aceite; `newly_accepted` é falso quando a versão já estava aceita."""

    version: str
    accepted_at: datetime
    newly_accepted: bool


class TermsChannel(StrEnum):
    """Por onde o aceite chegou (ADR 029 e o adendo de 2026-11-05).

    `WEB` é a pessoa na sessão do navegador; `ADMIN_CLI` é o operador pelo `gs-admin
    accept-terms`, para as contas que ele mesmo opera. Só o segundo vai para o `details` do
    evento: os aceites anteriores à migração 0017 são todos da sessão e ficaram sem detalhe.
    """

    WEB = "web"
    ADMIN_CLI = "admin_cli"


def record_terms_acceptance(
    session: Session,
    owner_id: UUID,
    *,
    version: str,
    now: datetime,
    channel: TermsChannel,
    ip: str | None,
) -> TermsAcceptance:
    """Aceita `version` pela conta e audita o aceite novo como `TERMS_ACCEPTED`, na mesma transação.

    O caminho único da sessão web e do `gs-admin`: repetir não grava nem audita de novo, e a
    conta inexistente levanta `UnknownAccountError` sem auditar.
    """
    accepted = accept_terms(session, owner_id, version=version, now=now)
    if accepted.newly_accepted:
        record_audit_event(
            session,
            event=AuditEventType.TERMS_ACCEPTED,
            user_id=owner_id,
            ip=ip,
            outcome=AuditOutcome.SUCCESS,
            now=now,
            details=None if channel is TermsChannel.WEB else {"channel": channel.value},
        )
    return accepted


def accept_terms(
    session: Session, owner_id: UUID, *, version: str, now: datetime
) -> TermsAcceptance:
    """Registra o aceite de `version` pela conta; repetir mantém a data do primeiro aceite.

    Um `UPDATE` condicional, sem "ler e depois gravar": de dois aceites simultâneos, um grava e
    o outro, depois do commit do primeiro, encontra a versão já aceita e só a devolve.
    """
    updated = session.execute(
        update(app_user)
        .where(app_user.c.id == owner_id, app_user.c.terms_version.is_distinct_from(version))
        .values(terms_version=version, terms_accepted_at=now)
        .returning(app_user.c.terms_accepted_at)
    ).first()
    if updated is not None:
        return TermsAcceptance(version, updated.terms_accepted_at, newly_accepted=True)
    stored = session.execute(
        select(app_user.c.terms_accepted_at).where(
            app_user.c.id == owner_id, app_user.c.terms_version == version
        )
    ).first()
    if stored is None:
        raise UnknownAccountError(f"conta inexistente: {owner_id}")
    return TermsAcceptance(version, stored.terms_accepted_at, newly_accepted=False)
