"""Aceite versionado dos termos (fatia 8): evento `TERMS_ACCEPTED` na auditoria.

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-14 23:00:00.000000

Lembrete (docs/GoblinShiftPlan.md, 3.8): expand/contract. As colunas `terms_version` e
`terms_accepted_at` já existem desde a 0006; aqui só o CHECK do tipo de evento ganha um valor,
que o código anterior nunca grava (ADR 029). O downgrade apaga esses eventos, que o CHECK antigo
recusaria; a versão aceita continua na conta.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0008"
down_revision: str | Sequence[str] | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CONSTRAINT = "audit_event_event_type_check"
_TYPES_0006 = (
    "'LOGIN_SUCCEEDED', 'LOGIN_FAILED', 'LOGOUT', 'LOGOUT_ALL', 'KEY_ISSUED', 'KEY_REVOKED', "
    "'REAUTH_REQUIRED', 'CSRF_FAILED', 'ACCESS_DENIED', 'ACCOUNT_DELETION_REQUESTED', "
    "'ACCOUNT_BLOCKED', 'ACCOUNT_UNBLOCKED'"
)
_TYPES_0008 = _TYPES_0006 + ", 'TERMS_ACCEPTED'"


def upgrade() -> None:
    _replace_event_type_check(_TYPES_0008)


def downgrade() -> None:
    op.execute("DELETE FROM audit_event WHERE event_type = 'TERMS_ACCEPTED'")
    _replace_event_type_check(_TYPES_0006)


def _replace_event_type_check(types: str) -> None:
    # `NOT VALID` + `VALIDATE` evitaria o bloqueio da tabela durante a varredura; a auditoria guarda
    # 90 dias de eventos de segurança, poucos o bastante para validar de uma vez.
    op.drop_constraint(op.f(_CONSTRAINT), "audit_event", type_="check")
    op.create_check_constraint(op.f(_CONSTRAINT), "audit_event", f"event_type IN ({types})")
