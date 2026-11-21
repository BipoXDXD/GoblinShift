"""Candidato amarrado à tentativa: FK de `attempt_id` e CHECK do par (#442, 23a e 23b).

Revision ID: 0020
Revises: 0019
Create Date: 2026-11-21 12:00:00.000000

Lembrete (docs/GoblinShiftPlan.md, 3.8): expand/contract. Só constraints novas, que o código
anterior já cumpre: o worker cria o candidato com a tentativa que acabou de reivindicar e a geração
dela (`attempt.generation` e `claimed.generation` vêm do mesmo claim), e nunca troca uma sem a
outra.

- `stored_object_attempt_id_fkey`: `(attempt_id, generation)` aponta para `attempt (id, generation)`
  com `ON DELETE SET NULL`. Sem ela, a purga da Run apaga a tentativa e deixa o candidato
  apontando para o nada. É composta porque o PostgreSQL só aceita no `SET NULL (...)` colunas da
  própria FK: com a FK só em `attempt_id`, a purga anularia `attempt_id` e deixaria `generation`,
  violando a CHECK abaixo. De quebra, o banco garante que a geração do candidato é a da tentativa,
  que é o que o fencing da publicação supõe. Com MATCH SIMPLE, um par com uma coluna nula não é
  conferido pela FK; a CHECK recusa esse par. A checagem da FK ao apagar uma tentativa usa o índice
  parcial `stored_object_attempt_id_idx` (0019).
- `attempt_id_generation_key`: `UNIQUE (id, generation)` em `attempt`, que a FK composta exige.
  É trivialmente verdadeira (`id` é a PK). O índice nasce `CONCURRENTLY`, fora da transação, e vira
  constraint com `USING INDEX`, que só grava no catálogo. Um retry depois de timeout do
  `CONCURRENTLY` acha o índice inválido e o refaz (`DROP INDEX CONCURRENTLY IF EXISTS`, ADR 057).
- `stored_object_attempt_generation_check`: `(attempt_id IS NULL) = (generation IS NULL)`. Um
  candidato sem geração quebra o fencing da publicação.

A FK e a CHECK entram `NOT VALID` (ADR 057; decisão D4 do plano): só gravam no catálogo, sem varrer
`stored_object`, e valem para toda linha nova a partir daqui. O reparo do legado e o `VALIDATE`
ficam na 0021, numa transação própria: com a FK já de pé, nenhuma exclusão de tentativa deixa
órfão novo entre o reparo e a validação. O downgrade só remove constraints e o índice, sem perder
dado.

Antes do merge, o operador conta em produção o que a 0021 vai reparar (decisão D2):

    SELECT count(*) FILTER (
             WHERE attempt_id IS NOT NULL
               AND NOT EXISTS (SELECT FROM attempt a WHERE a.id = o.attempt_id)
           ) AS orfaos,
           count(*) FILTER (
             WHERE EXISTS (
                     SELECT FROM attempt a
                      WHERE a.id = o.attempt_id AND a.generation <> o.generation
                   )
           ) AS geracao_divergente,
           count(*) FILTER (WHERE (attempt_id IS NULL) <> (generation IS NULL)) AS par_quebrado
      FROM stored_object o;
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0020"
down_revision: str | Sequence[str] | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ATTEMPT_KEY = "attempt_id_generation_key"
_ATTEMPT_FK = "stored_object_attempt_id_fkey"
_PAIR_CHECK = "stored_object_attempt_generation_check"


def upgrade() -> None:
    with op.get_context().autocommit_block():
        # lock-ok: limpeza do índice inválido de uma tentativa anterior (ADR 057)
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS attempt_id_generation_key")
        # lock-ok: CONCURRENTLY não bloqueia a escrita em attempt
        op.execute(
            "CREATE UNIQUE INDEX CONCURRENTLY attempt_id_generation_key ON attempt (id, generation)"
        )
    # lock-ok: USING INDEX só grava no catálogo; ACCESS EXCLUSIVE de um instante em attempt
    op.execute(
        "ALTER TABLE attempt ADD CONSTRAINT attempt_id_generation_key "
        "UNIQUE USING INDEX attempt_id_generation_key"
    )
    # SHARE ROW EXCLUSIVE breve em stored_object e attempt, sob o lock_timeout do ADR 057.
    # lock-ok: FK NOT VALID só grava no catálogo, sem varrer stored_object
    op.create_foreign_key(
        op.f(_ATTEMPT_FK),
        "stored_object",
        "attempt",
        ["attempt_id", "generation"],
        ["id", "generation"],
        ondelete="SET NULL",
        postgresql_not_valid=True,
    )
    # ACCESS EXCLUSIVE de um instante em stored_object, sob o lock_timeout do ADR 057.
    # lock-ok: CHECK NOT VALID só grava no catálogo, sem varrer stored_object
    op.create_check_constraint(
        op.f(_PAIR_CHECK),
        "stored_object",
        "(attempt_id IS NULL) = (generation IS NULL)",
        postgresql_not_valid=True,
    )


def downgrade() -> None:
    op.drop_constraint(op.f(_PAIR_CHECK), "stored_object", type_="check")
    op.drop_constraint(op.f(_ATTEMPT_FK), "stored_object", type_="foreignkey")
    op.drop_constraint(op.f(_ATTEMPT_KEY), "attempt", type_="unique")
