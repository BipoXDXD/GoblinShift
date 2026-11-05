"""Canal do aceite dos termos: o `TERMS_ACCEPTED` do `gs-admin accept-terms` marca a origem.

Revision ID: 0017
Revises: 0016
Create Date: 2026-11-05 08:00:00.000000

Lembrete (docs/GoblinShiftPlan.md, 3.8): expand/contract. Só o CHECK `audit_event_details_check`
muda, e só para aceitar mais: no `TERMS_ACCEPTED`, o detalhe pode ser NULL (aceite pela sessão
web, como todos os eventos já gravados e os que a API anterior grava) ou exatamente
`{"channel": "admin_cli"}` (aceite registrado pelo operador; ADR 029, adendo de 2026-11-05). Valor
fixo comparado por igualdade, para nada além do canal, e nenhum dado pessoal, entrar por ali.

O CHECK recriado varre a tabela sob `ACCESS EXCLUSIVE`, como na 0016 e pelos mesmos motivos: a
tabela guarda só 90 dias de eventos, a varredura leva milissegundos e o `lock_timeout` de 2 s do
`env.py` faz a migração falhar alto em vez de enfileirar requisições.

O downgrade anula o canal, que o CHECK antigo recusaria, e mantém os eventos: o aceite continua
auditado, só sem a origem.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0017"
down_revision: str | Sequence[str] | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DETAILS_CHECK = "audit_event_details_check"
_LIMITS_BRANCH = (
    "WHEN event_type = 'ACCOUNT_LIMITS_CHANGED' "
    "THEN coalesce(jsonb_typeof(details) = 'object', false) "
)
_DETAILS_0016 = f"CASE {_LIMITS_BRANCH}ELSE details IS NULL END"
_DETAILS_0017 = (
    f"CASE {_LIMITS_BRANCH}"
    "WHEN event_type = 'TERMS_ACCEPTED' "
    """THEN details IS NULL OR details = '{"channel": "admin_cli"}'::jsonb """
    "ELSE details IS NULL END"
)


def upgrade() -> None:
    _replace_details_check(_DETAILS_0017)


def downgrade() -> None:
    op.execute(
        "UPDATE audit_event SET details = NULL "
        "WHERE event_type = 'TERMS_ACCEPTED' AND details IS NOT NULL"
    )
    _replace_details_check(_DETAILS_0016)


def _replace_details_check(condition: str) -> None:
    op.drop_constraint(op.f(_DETAILS_CHECK), "audit_event", type_="check")
    op.create_check_constraint(op.f(_DETAILS_CHECK), "audit_event", condition)
