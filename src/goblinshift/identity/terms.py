"""Aceite versionado dos termos de uso e do aviso de privacidade (1.8; ADR 029).

A conta guarda a última versão aceita e a data do aceite (`app_user.terms_version` e
`terms_accepted_at`, migração 0006); cada aceite novo também vira evento de auditoria. A versão
vigente é uma constante do servidor, nunca um valor do cliente: trocar o texto em `docs/legal`
sobe esta constante, e toda conta precisa aceitar de novo.
"""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from goblinshift.identity.accounts import UnknownAccountError
from goblinshift.identity.tables import app_user

# "rascunho" até a revisão jurídica e o domínio definitivo (plano da fatia 8, A1 e F8-5).
CURRENT_TERMS_VERSION = "v1-rascunho"


@dataclass(frozen=True)
class TermsAcceptance:
    """Resultado do aceite; `newly_accepted` é falso quando a versão já estava aceita."""

    version: str
    accepted_at: datetime
    newly_accepted: bool


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
