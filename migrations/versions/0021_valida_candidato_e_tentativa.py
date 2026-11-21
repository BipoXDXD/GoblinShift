"""Repara o par `attempt_id`/`generation` e valida as constraints da 0020 (#442, 23a e 23b).

Revision ID: 0021
Revises: 0020
Create Date: 2026-11-21 12:00:00.000000

Lembrete (docs/GoblinShiftPlan.md, 3.8): expand/contract. Numa transação só, a do Alembic:

1. Candidato que aponta para tentativa inexistente (a purga da Run a apagou antes da FK) perde
   `attempt_id` e `generation`, como o `SET NULL` da FK faria.
2. Candidato cuja geração diverge da geração da tentativa perde as duas colunas, pela mesma regra:
   a FK composta não acha a tentativa `(id, generation)`, o mesmo que um órfão.
3. Candidato com tentativa e sem geração recebe a geração da própria tentativa.
4. Objeto sem tentativa e com geração perde a geração.
5. `VALIDATE CONSTRAINT` da FK e da CHECK.

O reparo fica aqui, e não na 0020, porque só depois da FK de pé nenhuma exclusão de tentativa
deixa órfão novo entre o reparo e a validação. Os `UPDATE` tomam `ROW EXCLUSIVE` e o `VALIDATE`,
`SHARE UPDATE EXCLUSIVE` (mais `ROW SHARE` em `attempt`, na FK): nenhum deles bloqueia leitura
nem escrita da API. A consulta que conta em produção o que este reparo muda está na docstring da
0020 (decisão D2 do plano).

O downgrade volta ao catálogo da 0020: remove as duas constraints e as recria `NOT VALID`. O reparo
não se desfaz: o ponteiro órfão e a geração faltante não têm como voltar, e o estado reparado já é
o certo para o código atual e para o anterior.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0021"
down_revision: str | Sequence[str] | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ATTEMPT_FK = "stored_object_attempt_id_fkey"
_PAIR_CHECK = "stored_object_attempt_generation_check"


def upgrade() -> None:
    # lock-ok: UPDATE toma ROW EXCLUSIVE, que não bloqueia a API; só os órfãos do legado mudam
    op.execute(
        """
        UPDATE stored_object
           SET attempt_id = NULL, generation = NULL
         WHERE attempt_id IS NOT NULL
           AND NOT EXISTS (SELECT FROM attempt WHERE attempt.id = stored_object.attempt_id)
        """
    )
    # lock-ok: UPDATE toma ROW EXCLUSIVE, que não bloqueia a API; só a geração divergente muda
    op.execute(
        """
        UPDATE stored_object AS o
           SET attempt_id = NULL, generation = NULL
          FROM attempt AS a
         WHERE o.attempt_id = a.id
           AND o.generation <> a.generation
        """
    )
    # lock-ok: UPDATE toma ROW EXCLUSIVE, que não bloqueia a API; só os candidatos sem geração
    op.execute(
        """
        UPDATE stored_object AS o
           SET generation = a.generation
          FROM attempt AS a
         WHERE o.attempt_id = a.id
           AND o.generation IS NULL
        """
    )
    # lock-ok: UPDATE toma ROW EXCLUSIVE, que não bloqueia a API; só a geração sem tentativa
    op.execute(
        """
        UPDATE stored_object
           SET generation = NULL
         WHERE attempt_id IS NULL
           AND generation IS NOT NULL
        """
    )
    # Na FK, também ROW SHARE em attempt, que tampouco bloqueia escrita.
    # lock-ok: VALIDATE toma SHARE UPDATE EXCLUSIVE, que não bloqueia leitura nem escrita
    op.execute("ALTER TABLE stored_object VALIDATE CONSTRAINT stored_object_attempt_id_fkey")
    # lock-ok: VALIDATE toma SHARE UPDATE EXCLUSIVE, que não bloqueia leitura nem escrita
    op.execute(
        "ALTER TABLE stored_object VALIDATE CONSTRAINT stored_object_attempt_generation_check"
    )


def downgrade() -> None:
    op.drop_constraint(op.f(_PAIR_CHECK), "stored_object", type_="check")
    op.create_check_constraint(
        op.f(_PAIR_CHECK),
        "stored_object",
        "(attempt_id IS NULL) = (generation IS NULL)",
        postgresql_not_valid=True,
    )
    op.drop_constraint(op.f(_ATTEMPT_FK), "stored_object", type_="foreignkey")
    op.create_foreign_key(
        op.f(_ATTEMPT_FK),
        "stored_object",
        "attempt",
        ["attempt_id", "generation"],
        ["id", "generation"],
        ondelete="SET NULL",
        postgresql_not_valid=True,
    )
