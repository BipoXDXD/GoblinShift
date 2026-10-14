"""Conta a partir da identidade externa do login (docs/GoblinShiftPlan.md, 1.8 e 3.6; Frente A).

A identidade é `(issuer, subject)`, nunca e-mail (ADR 008). Com o cadastro fechado (ADR 022),
identidade nova nasce `blocked` e só entra depois do `gs-admin unblock`.

Contrato fixado na Fase 0 da fatia 6; os corpos são da Frente A.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum, auto
from uuid import UUID

from sqlalchemy.orm import Session


@dataclass(frozen=True)
class ExternalIdentity:
    """Identidade já validada e normalizada pelo provedor (Google: `sub`; GitHub: `id`)."""

    issuer: str
    subject: str
    display_name: str


class LoginRefused(Enum):
    """Motivo interno da recusa; o navegador recebe sempre o mesmo erro genérico."""

    BLOCKED = auto()
    PENDING_APPROVAL = auto()
    DELETION_REQUESTED = auto()


def resolve_login(
    session: Session, identity: ExternalIdentity, *, signup_open: bool, now: datetime
) -> UUID | LoginRefused:
    """Cria ou atualiza a conta da identidade e decide se o login pode seguir."""
    raise NotImplementedError("Frente A da fatia 6")
