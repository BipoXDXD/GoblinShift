"""Chaves de API: `Idempotency-Key` opcional na emissão pela sessão, `api_key.idem_key` (#442).

Revision ID: 0031
Revises: 0030
Create Date: 2026-11-27 12:00:00.000000

Lembrete (docs/GoblinShiftPlan.md, 3.8): expand/contract. A coluna nova é anulável e sem default:
o INSERT do código anterior não a cita e grava nulo, e as chaves existentes ficam sem `idem_key`.
O CHECK e o índice único só olham as linhas com `idem_key`, que o código anterior nunca grava.

`idem_key` guarda a `Idempotency-Key` do `POST /api-keys` (57b), no formato de `runs.acceptance`,
o mesmo CHECK de `idempotency_record`. A repetição na mesma conta responde 409 com o id da chave
já emitida: a busca é feita sob a trava da conta (`accounts._issue_locked`), e o índice único
parcial `(owner_id, idem_key)` fica de rede. Nada do token é guardado.

Travas (ADR 057): o ADD COLUMN anulável e sem default só muda o catálogo. O CHECK varre a tabela
sob `ACCESS EXCLUSIVE`, sem `NOT VALID` + `VALIDATE`, porque a coluna acabou de nascer toda nula.
O índice comum, na transação do Alembic, basta, como na 0026: a emissão pela sessão para em
`MAX_STORED_KEYS` (100) chaves por conta, e a manutenção apaga as inativas depois de
`KEY_RETENTION`. O `env.py` usa o `make_engine`, com `lock_timeout` de 2 s: se uma transação longa
segurar `api_key`, a migração falha alto em vez de enfileirar autenticações, e basta rodá-la de
novo.

O downgrade apaga a coluna, e com ela as `Idempotency-Key` gravadas (as chaves continuam). Se
alguma chave tiver `idem_key`, ele só roda com `GS_ALLOW_LOSSY_DOWNGRADE=1`.
"""

import os
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0031"
down_revision: str | Sequence[str] | None = "0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_IDEM_INDEX = "api_key_owner_id_idem_key_idx"
_IDEM_CHECK = "api_key_idem_key_check"


def upgrade() -> None:
    # lock-ok: coluna nula sem default; só muda o catálogo
    op.add_column("api_key", sa.Column("idem_key", sa.Text(), nullable=True))
    # lock-ok: idem_key recém-criada e toda nula; api_key tem no máximo 100 chaves por conta
    op.create_check_constraint(op.f(_IDEM_CHECK), "api_key", "idem_key ~ '^[A-Za-z0-9_-]{16,64}$'")
    # lock-ok: índice parcial em linhas que ainda não existem; api_key é pequena, como na 0026
    op.create_index(
        op.f(_IDEM_INDEX),
        "api_key",
        ["owner_id", "idem_key"],
        unique=True,
        postgresql_where=sa.text("idem_key IS NOT NULL"),
    )


def downgrade() -> None:
    if os.environ.get("GS_ALLOW_LOSSY_DOWNGRADE") != "1":
        op.execute(
            "DO $$ BEGIN IF EXISTS (SELECT FROM api_key WHERE idem_key IS NOT NULL) THEN "
            "RAISE EXCEPTION 'o downgrade da 0031 apagaria as Idempotency-Key das chaves de API; "
            "GS_ALLOW_LOSSY_DOWNGRADE=1 aceita a perda'; END IF; END $$"
        )
    op.drop_index(op.f(_IDEM_INDEX), table_name="api_key")
    op.drop_constraint(op.f(_IDEM_CHECK), "api_key", type_="check")
    op.drop_column("api_key", "idem_key")
