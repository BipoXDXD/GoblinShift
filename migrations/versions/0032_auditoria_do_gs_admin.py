"""Auditoria do `gs-admin`: quem agiu (`audit_event.actor`) e os eventos novos da CLI (#442, 50c).

Revision ID: 0032
Revises: 0030
Create Date: 2026-11-27 21:00:00.000000

Lembrete (docs/GoblinShiftPlan.md, 3.8): expand/contract. A coluna nova é anulável e sem default:
o INSERT do código anterior não a cita e grava nulo. O CHECK do tipo de evento ganha três valores,
que o código anterior nunca grava.

`actor` diz quem agiu: `user` (o titular, pela API ou pelo login), `admin` (o operador, pelo
`gs-admin`) ou `system`. Antes dela, o `ACCOUNT_DELETION_REQUESTED` do `gs-admin` e o do titular
só se distinguiam por `ip_prefix IS NULL`, que a API também grava quando falta `request.client`.
Nulo é evento anterior a esta migração: nenhum UPDATE preenche o passado, e nenhum papel de runtime
tem UPDATE nessa tabela (ADR 026). Os tipos novos: `ACCOUNT_CREATED` (`create-user`),
`ACCOUNT_LOOKED_UP` (`find-user`, que reidentifica o `user.hash`) e `ACCOUNT_APPROVED`
(`approve`, a liberação da conta pendente, separada do `unblock`).

Travas (ADR 057): o ADD COLUMN anulável e sem default só muda o catálogo. Os dois CHECKs (o de
`actor` e o do tipo, recriado) varrem a tabela sob `ACCESS EXCLUSIVE`, sem `NOT VALID` +
`VALIDATE`, como na 0016: `actor` acabou de nascer toda nula, e `audit_event` guarda só 90 dias de
eventos, poucos milhares de linhas numa VPS, varridos em milissegundos. O `env.py` usa o
`make_engine`, com `lock_timeout` de 2 s: se uma transação longa segurar a tabela, a migração
falha alto em vez de enfileirar logins atrás do `ACCESS EXCLUSIVE`, e basta rodá-la de novo.

O downgrade apaga os eventos dos tipos novos, que o CHECK antigo recusaria, e a coluna `actor`,
com quem agiu em cada evento. Se houver algum desses dados, ele só roda com
`GS_ALLOW_LOSSY_DOWNGRADE=1`.
"""

import os
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0032"
down_revision: str | Sequence[str] | None = "0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TYPE_CHECK = "audit_event_event_type_check"
_ACTOR_CHECK = "audit_event_actor_check"
_TYPES_0016 = (
    "'LOGIN_SUCCEEDED', 'LOGIN_FAILED', 'LOGOUT', 'LOGOUT_ALL', 'KEY_ISSUED', 'KEY_REVOKED', "
    "'REAUTH_REQUIRED', 'CSRF_FAILED', 'ACCESS_DENIED', 'ACCOUNT_DELETION_REQUESTED', "
    "'ACCOUNT_BLOCKED', 'ACCOUNT_UNBLOCKED', 'TERMS_ACCEPTED', 'ACCOUNT_LIMITS_CHANGED'"
)
_NEW_TYPES = "'ACCOUNT_CREATED', 'ACCOUNT_LOOKED_UP', 'ACCOUNT_APPROVED'"
_LOSSY = f"actor IS NOT NULL OR event_type IN ({_NEW_TYPES})"


def upgrade() -> None:
    _replace_event_type_check(f"{_TYPES_0016}, {_NEW_TYPES}")
    # lock-ok: coluna nula sem default; só muda o catálogo
    op.add_column("audit_event", sa.Column("actor", sa.Text(), nullable=True))
    # lock-ok: actor recém-criada e toda nula; audit_event tem poucos milhares de linhas (ms)
    op.create_check_constraint(
        op.f(_ACTOR_CHECK), "audit_event", "actor IN ('user', 'admin', 'system')"
    )


def downgrade() -> None:
    if os.environ.get("GS_ALLOW_LOSSY_DOWNGRADE") != "1":
        op.execute(
            f"DO $$ BEGIN IF EXISTS (SELECT FROM audit_event WHERE {_LOSSY}) THEN "
            "RAISE EXCEPTION 'o downgrade da 0032 apagaria quem agiu e os eventos novos da "
            "auditoria; GS_ALLOW_LOSSY_DOWNGRADE=1 aceita a perda'; END IF; END $$"
        )
    op.execute(
        "DELETE FROM audit_event "
        "WHERE event_type IN ('ACCOUNT_CREATED', 'ACCOUNT_LOOKED_UP', 'ACCOUNT_APPROVED')"
    )
    op.drop_constraint(op.f(_ACTOR_CHECK), "audit_event", type_="check")
    op.drop_column("audit_event", "actor")
    _replace_event_type_check(_TYPES_0016)


def _replace_event_type_check(types: str) -> None:
    # lock-ok: DROP CONSTRAINT de CHECK só muda o catálogo: lock de um instante, sem varredura
    op.drop_constraint(op.f(_TYPE_CHECK), "audit_event", type_="check")
    # lock-ok: audit_event guarda 90 dias, poucos milhares de linhas; varre em ms (docstring)
    op.create_check_constraint(op.f(_TYPE_CHECK), "audit_event", f"event_type IN ({types})")
