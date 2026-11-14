"""Índices das buscas da manutenção e da FK de `object_use` (#442) e marca de pendente refeita.

Revision ID: 0019
Revises: 0018
Create Date: 2026-11-14 12:00:00.000000

Lembrete (docs/GoblinShiftPlan.md, 3.8): expand/contract. Só índices novos, que o código anterior
ignora. As tabelas são pequenas (poucas contas), então o `CREATE INDEX` comum, dentro da
transação do Alembic, basta, como na 0010.

- `object_use(object_id)`, completo: o parcial `object_use_open_idx` só cobre os usos abertos e
  não serve à checagem RESTRICT da FK ao apagar um `stored_object`, nem ao "nenhum uso" da
  retenção dos arquivos.
- `stored_object(attempt_id)`, parcial: os candidatos de uma tentativa (descarte e retenção das
  Runs); objetos sem tentativa nunca são buscados por ela.
- `least(revoked_at, idle_expires_at, absolute_expires_at)` de `web_session`: a expressão idêntica
  ao predicado da limpeza de sessões, senão o planejador não a casa com o índice.
- `resource_reservation(released_at)`, parcial: a limpeza das reservas liberadas; as vivas, com
  `released_at` nulo, nunca entram nela.

A marca `awaiting_approval` (0018) é refeita. Na janela do deploy da 0018, a API antiga ainda
gravava sem conhecer a coluna: o cadastro fechado criava a conta pendente com o default `false`
(fora da fila de liberação), e o bloqueio ou a liberação de uma pendente deixavam a marca `true`.
A 0019 sai num deploy separado, com a API nova já no ar, e repete o backfill da 0018 nos dois
sentidos. A marcação só olha contas criadas há menos de 90 dias, a retenção da auditoria: numa
conta mais velha, o `ACCOUNT_BLOCKED` de um bloqueio pelo admin pode já ter sido apagado junto
com as sessões, e a falta de rastro deixa de distinguir o bloqueio de um cadastro pendente. As
contas da janela têm dias. Na API nova, toda conta marcada está bloqueada e sem `ACCOUNT_BLOCKED`,
e toda conta bloqueada nesse prazo, sem sessão nem `ACCOUNT_BLOCKED`, está marcada. Então os dois
`UPDATE` só mexem no que a API antiga deixou, e rodar a 0019 de novo não muda nada. O downgrade
não desfaz a marca: ela já é o estado certo para o código atual e para o anterior, que nunca a lê.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: str | Sequence[str] | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index("object_use_object_id_idx", "object_use", ["object_id"])
    op.create_index(
        "stored_object_attempt_id_idx",
        "stored_object",
        ["attempt_id"],
        postgresql_where=sa.text("attempt_id IS NOT NULL"),
    )
    op.create_index(
        "web_session_ended_at_idx",
        "web_session",
        [sa.text("least(revoked_at, idle_expires_at, absolute_expires_at)")],
    )
    op.create_index(
        "resource_reservation_released_at_idx",
        "resource_reservation",
        ["released_at"],
        postgresql_where=sa.text("released_at IS NOT NULL"),
    )
    op.execute(
        """
        UPDATE app_user AS u
           SET awaiting_approval = true
         WHERE u.status = 'blocked'
           AND NOT u.awaiting_approval
           -- 90 dias: AUDIT_RETENTION de identity/purge.py quando a 0019 foi escrita.
           AND u.created_at > now() - interval '90 days'
           AND NOT EXISTS (SELECT 1 FROM web_session AS s WHERE s.user_id = u.id)
           AND NOT EXISTS (
                 SELECT 1 FROM audit_event AS e
                  WHERE e.user_id = u.id AND e.event_type = 'ACCOUNT_BLOCKED'
               )
        """
    )
    op.execute(
        """
        UPDATE app_user AS u
           SET awaiting_approval = false
         WHERE u.awaiting_approval
           AND (
                 u.status <> 'blocked'
                 OR EXISTS (
                      SELECT 1 FROM audit_event AS e
                       WHERE e.user_id = u.id AND e.event_type = 'ACCOUNT_BLOCKED'
                    )
               )
        """
    )


def downgrade() -> None:
    op.drop_index("resource_reservation_released_at_idx", table_name="resource_reservation")
    op.drop_index("web_session_ended_at_idx", table_name="web_session")
    op.drop_index("stored_object_attempt_id_idx", table_name="stored_object")
    op.drop_index("object_use_object_id_idx", table_name="object_use")
