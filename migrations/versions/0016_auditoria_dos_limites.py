"""Auditoria do `gs-admin set-limits`: evento `ACCOUNT_LIMITS_CHANGED` e `audit_event.details`.

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-16 08:00:00.000000

Lembrete (docs/GoblinShiftPlan.md, 3.8): expand/contract. O CHECK do tipo de evento ganha um valor,
que o código anterior nunca grava, e a coluna nova é anulável e sem default: os eventos existentes
ficam sem detalhe, como o CHECK novo exige.

`details` existe só para o evento que precisa dele. Formato (ADR 026, `identity.audit`):
`{"changes": {<item>: {"old": <valor>, "new": <valor>}}}`, com um item por limite citado no
comando (`runs_per_24h`, `input_bytes_per_24h`, `storage_bytes`); `null` nas cotas é "sem
override". A conta-alvo é o `user_id` do evento, como no bloqueio. Só números e nomes fixos: nada
de dado pessoal, então a linha sai inteira aos 90 dias, sem anulação própria (1.7).

O CHECK `audit_event_details_check` amarra os dois: o tipo novo sempre tem detalhe, como objeto, e
os outros nunca. A auditoria guarda 90 dias de eventos, poucos o bastante para validar o CHECK de
uma vez, sem `NOT VALID`.

O downgrade apaga os eventos do tipo novo, que o CHECK antigo recusaria, e depois a coluna.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0016"
down_revision: str | Sequence[str] | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TYPE_CHECK = "audit_event_event_type_check"
_DETAILS_CHECK = "audit_event_details_check"
_TYPES_0008 = (
    "'LOGIN_SUCCEEDED', 'LOGIN_FAILED', 'LOGOUT', 'LOGOUT_ALL', 'KEY_ISSUED', 'KEY_REVOKED', "
    "'REAUTH_REQUIRED', 'CSRF_FAILED', 'ACCESS_DENIED', 'ACCOUNT_DELETION_REQUESTED', "
    "'ACCOUNT_BLOCKED', 'ACCOUNT_UNBLOCKED', 'TERMS_ACCEPTED'"
)
_TYPES_0016 = _TYPES_0008 + ", 'ACCOUNT_LIMITS_CHANGED'"


def upgrade() -> None:
    _replace_event_type_check(_TYPES_0016)
    op.add_column(
        "audit_event",
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.create_check_constraint(
        op.f(_DETAILS_CHECK),
        "audit_event",
        "CASE WHEN event_type = 'ACCOUNT_LIMITS_CHANGED' "
        "THEN coalesce(jsonb_typeof(details) = 'object', false) "
        "ELSE details IS NULL END",
    )


def downgrade() -> None:
    op.execute("DELETE FROM audit_event WHERE event_type = 'ACCOUNT_LIMITS_CHANGED'")
    op.drop_constraint(op.f(_DETAILS_CHECK), "audit_event", type_="check")
    op.drop_column("audit_event", "details")
    _replace_event_type_check(_TYPES_0008)


def _replace_event_type_check(types: str) -> None:
    op.drop_constraint(op.f(_TYPE_CHECK), "audit_event", type_="check")
    op.create_check_constraint(op.f(_TYPE_CHECK), "audit_event", f"event_type IN ({types})")
