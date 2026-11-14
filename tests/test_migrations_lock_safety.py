"""Migration nova não segura lock que trava a API sem declarar por quê (ADR 057).

As migrations até a 0018 são anteriores ao check e ficam de fora; o `downgrade()` também, como
no check de contract. As regras estão em `tests/migration_lock_check.py`.
"""

import time
from pathlib import Path

import pytest

from tests.migration_lock_check import (
    REVISION_FILE,
    checked_revisions,
    concurrent_in_transaction_lines,
    concurrent_index_without_cleanup_lines,
    unmarked_blocking_lines,
)

VERSIONS = Path(__file__).resolve().parents[1] / "migrations" / "versions"
# Folga larga para a máquina do CI: o caso linear leva milissegundos.
MAX_ANALYSIS_S = 2.0


def test_migrations_novas_so_bloqueiam_com_a_marca_lock_ok() -> None:
    problems = {}
    for name in checked_revisions([path.name for path in VERSIONS.glob("*.py")]):
        source = (VERSIONS / name).read_text()
        blocking = unmarked_blocking_lines(source)
        concurrent = concurrent_in_transaction_lines(source)
        without_cleanup = concurrent_index_without_cleanup_lines(source)
        if blocking or concurrent or without_cleanup:
            problems[name] = {
                "sem lock-ok": blocking,
                "CONCURRENTLY dentro de transação": concurrent,
                "índice CONCURRENTLY sem DROP IF EXISTS antes": without_cleanup,
            }

    assert problems == {}


def test_todo_arquivo_de_migration_comeca_pelo_numero_da_revisao() -> None:
    # Um nome fora do padrão escaparia do check em silêncio.
    names = [path.name for path in VERSIONS.glob("*.py")]

    assert [name for name in names if not REVISION_FILE.match(name)] == []


def test_check_cobre_da_0019_em_diante() -> None:
    names = ["0001_a.py", "0018_b.py", "0019_c.py", "0020_d.py", "README.md"]

    assert checked_revisions(names) == ["0019_c.py", "0020_d.py"]


@pytest.mark.parametrize(
    "operation",
    [
        'op.create_index("ix_run_owner", "run", ["owner_id"])',
        'op.create_index("ix_run_owner", "run", ["owner_id"], unique=True)',
        'op.create_unique_constraint("uq_run_key", "run", ["key"])',
        'op.create_primary_key("pk_run", "run", ["id"])',
        'op.create_exclude_constraint("ex_run", "run", ("period", "&&"))',
        'op.create_foreign_key("fk_run_owner", "run", "account", ["owner_id"], ["id"])',
        'op.create_check_constraint("ck_run_size", "run", "size > 0")',
        'op.create_check_constraint("ck", "run", "size > 0", postgresql_not_valid=False)',
        'op.alter_column("run", "status", nullable=False)',
        'op.alter_column("run", "size", type_=sa.BigInteger())',
        (
            'op.add_column("run", sa.Column("token", sa.Uuid(),'
            ' server_default=sa.text("gen_random_uuid()")))'
        ),
        'op.add_column("run", sa.Column("seq", sa.BigInteger(), sa.Identity()))',
        (
            'op.add_column("run", sa.Column("at", sa.DateTime(),'
            ' server_default=sa.text("clock_timestamp()")))'
        ),
        'op.execute("CREATE INDEX ix_run_owner ON run (owner_id)")',
        'op.execute("CREATE UNIQUE INDEX ix_run_key ON run (key)")',
        'op.execute("REINDEX TABLE run")',
        (
            'op.execute("ALTER TABLE run ADD CONSTRAINT fk_owner FOREIGN KEY (owner_id)'
            ' REFERENCES account (id)")'
        ),
        'op.execute("ALTER TABLE run ADD CHECK (size > 0)")',
        'op.execute("ALTER TABLE run ADD CONSTRAINT uq_key UNIQUE (key)")',
        'op.execute("ALTER TABLE run ADD PRIMARY KEY (id)")',
        'op.execute("ALTER TABLE run ALTER COLUMN status SET NOT NULL")',
        'op.execute("ALTER TABLE run ALTER COLUMN size TYPE bigint")',
        'op.execute("ALTER TABLE run ALTER size SET DATA TYPE bigint")',
        'op.execute("ALTER TABLE run ADD COLUMN token uuid DEFAULT gen_random_uuid()")',
        'op.execute("ALTER TABLE run ADD COLUMN seq bigint GENERATED ALWAYS AS IDENTITY")',
        'op.execute("ALTER TABLE run ADD COLUMN seq bigserial")',
        'op.execute("VACUUM FULL run")',
        'op.execute("CLUSTER run USING ix_run_owner")',
        'op.execute(sa.text("create index ix_run_owner on run (owner_id)"))',
        'op.add_column("run", sa.Column("key", sa.Text(), unique=True))',
        'op.add_column("run", sa.Column("key", sa.Text(), index=True))',
        'op.add_column("run", sa.Column("id2", sa.BigInteger(), primary_key=True))',
        (
            'op.add_column("run", sa.Column("token", sa.Uuid(),'
            " server_default=sa.func.gen_random_uuid()))"
        ),
        'op.add_column("run", sa.Column("total", sa.Integer(), sa.Computed("a + b")))',
        'op.execute("ALTER TABLE run ADD COLUMN key text UNIQUE")',
        'op.execute("ALTER TABLE run ADD COLUMN id2 bigint PRIMARY KEY")',
        'op.execute("ALTER TABLE run ADD COLUMN t int GENERATED ALWAYS AS (a + b) STORED")',
        'op.execute("ALTER TABLE run ADD COLUMN x text, ADD COLUMN k uuid DEFAULT uuidv7()")',
        'op.execute("ALTER TABLE run ADD CONSTRAINT fk FOREIGN KEY (o) REFERENCES a -- NOT VALID")',
        'op.execute("ALTER TABLE run ADD CHECK (size > 0) /* NOT VALID */")',
        'op.execute(f"ALTER TABLE run ALTER COLUMN {column} TYPE bigint")',
        'op.execute(f"ALTER TABLE run ADD COLUMN {name} uuid DEFAULT gen_random_uuid()")',
        'op.execute("ALTER TABLE run ALTER COLUMN " + column + " TYPE bigint")',
        'op.add_column("run", sa.Column("u", sa.Integer(), sa.ForeignKey("account.id")))',
        'op.add_column("run", sa.Column("n", sa.Integer(), sa.CheckConstraint("n >= 0")))',
        'op.execute("ALTER TABLE run ADD COLUMN u int REFERENCES account (id)")',
        'op.execute("ALTER TABLE run ADD COLUMN n int DEFAULT 0 CHECK (n >= 0)")',
        'op.execute("LOCK TABLE run")',
        'op.execute("LOCK run IN ACCESS EXCLUSIVE MODE")',
        'op.execute("REFRESH MATERIALIZED VIEW run_summary")',
        'op.execute("ALTER TABLE run SET LOGGED")',
        'op.get_bind().exec_driver_sql("CREATE INDEX ix ON run (a)")',
        'create_index("ix_run_owner", "run", ["owner_id"])',
        "op.execute(\"ALTER TABLE run ALTER n SET DEFAULT 'a--b', ALTER n SET NOT NULL\")",
        'ddl = "SET NOT NULL"; op.execute(f"ALTER TABLE run ALTER COLUMN c {ddl}")',
        'op.execute("CREATE INDEX ix ON {} (c)".format(table))',
        'op.execute("CREATE INDEX ix ON %s (c)" % table)',
        'op.execute(sqltext="ALTER TABLE run ALTER COLUMN c SET NOT NULL")',
        'op.execute(sa.text("ALTER TABLE run ALTER c SET NOT NULL").bindparams(x=1))',
        'sql = "ALTER TABLE run "; sql += "ALTER COLUMN c SET NOT NULL"; op.execute(sql)',
        "op.execute(\"ALTER TABLE run ADD CONSTRAINT ck CHECK (s <> 'NOT VALID')\")",
        "op.execute('ALTER TABLE \"a--b\" ALTER COLUMN c SET NOT NULL')",
        (
            "op.execute(\"ALTER TABLE run ADD CONSTRAINT ck CHECK (s <> $c$don't$c$),"
            ' ADD CONSTRAINT fk FOREIGN KEY (o) REFERENCES a NOT VALID")'
        ),
        "op.execute('ALTER TABLE run ALTER COLUMN \"my col\" TYPE bigint')",
        'op.execute("ALTER TABLE run SET TABLESPACE fast")',
        'op.execute("ALTER TABLE run ATTACH PARTITION run_2027 FOR VALUES IN (2027)")',
        'op.execute("ALTER TABLE run ADD CONSTRAINT ck CHECK (s <> $$NOT VALID$$)")',
        # Na string de escape E'...', a barra invertida escapa a aspa e o texto continua.
        (
            "op.execute(\"ALTER TABLE run ALTER COLUMN c SET DEFAULT E'a\\\\'b',"
            ' ALTER COLUMN c SET NOT NULL")'
        ),
        # `$` dentro de identificador não abre dollar quoting.
        'op.execute("ALTER TABLE run$x$ ALTER COLUMN c SET NOT NULL")',
    ],
)
def test_ddl_bloqueante_sem_marca_e_recusado(operation: str) -> None:
    source = f"def upgrade() -> None:\n    {operation}\n"

    assert unmarked_blocking_lines(source) == [2]


@pytest.mark.parametrize(
    "operation",
    [
        'op.create_index("ix", "run", ["a"], postgresql_concurrently=True)',
        ('op.create_foreign_key("fk", "run", "account", ["o"], ["id"], postgresql_not_valid=True)'),
        'op.create_check_constraint("ck", "run", "size > 0", postgresql_not_valid=True)',
        'op.alter_column("run", "status", nullable=True)',
        'op.add_column("run", sa.Column("x", sa.Text()))',
        'op.add_column("run", sa.Column("on", sa.Boolean(), server_default=sa.false()))',
        'op.add_column("run", sa.Column("at", sa.DateTime(), server_default=sa.text("now()")))',
        'op.execute("CREATE INDEX CONCURRENTLY ix ON run (a)")',
        'op.execute("REINDEX INDEX CONCURRENTLY ix")',
        (
            'op.execute("ALTER TABLE run ADD CONSTRAINT fk FOREIGN KEY (o)'
            ' REFERENCES account (id) NOT VALID")'
        ),
        'op.execute("ALTER TABLE run VALIDATE CONSTRAINT fk")',
        'op.execute("ALTER TABLE run ADD CONSTRAINT uq UNIQUE USING INDEX ix")',
        'op.execute("ALTER TABLE run ALTER COLUMN status DROP NOT NULL")',
        "op.execute(\"ALTER TYPE run_status ADD VALUE 'paused'\")",
        'op.execute("UPDATE run SET size = 0 WHERE size IS NULL")',
        'op.add_column("run", sa.Column("t", sa.Integer(), sa.Computed("a", persisted=False)))',
        'op.execute("ALTER TABLE run ADD COLUMN x text, ADD COLUMN y int DEFAULT 0")',
        'op.execute("ALTER TABLE run ADD COLUMN t int GENERATED ALWAYS AS (a) VIRTUAL")',
        "op.execute(\"ALTER TABLE run ADD COLUMN n text DEFAULT 'a,b' -- UNIQUE\")",
        'op.execute("REFRESH MATERIALIZED VIEW CONCURRENTLY run_summary")',
        "op.execute(\"COMMENT ON TABLE run IS 'run CREATE INDEX later'\")",
    ],
    ids=[
        "indice-concorrente",
        "fk-not-valid",
        "check-not-valid",
        "nullable-true",
        "coluna-sem-default",
        "default-constante",
        "default-estavel",
        "sql-indice-concorrente",
        "sql-reindex-concorrente",
        "sql-fk-not-valid",
        "sql-validate",
        "sql-unique-using-index",
        "sql-drop-not-null",
        "sql-alter-type-enum",
        "sql-update",
        "coluna-gerada-virtual",
        "sql-varias-colunas-estaveis",
        "sql-coluna-gerada-virtual",
        "sql-virgula-entre-aspas-e-comentario",
        "sql-refresh-concorrente",
        "sql-ddl-so-dentro-de-texto",
    ],
)
def test_forma_segura_passa(operation: str) -> None:
    source = f"def upgrade() -> None:\n    {operation}\n"

    assert unmarked_blocking_lines(source) == []


@pytest.mark.parametrize(
    "source",
    [
        'def upgrade() -> None:\n    op.create_index("ix", "run", ["a"])  # lock-ok: tabela nova\n',
        'def upgrade() -> None:\n    # lock-ok: nova\n    op.create_index("i", "t", ["a"])\n',
        'def downgrade() -> None:\n    op.create_index("ix", "run", ["a"])\n',
    ],
    ids=["marca-na-linha", "marca-acima", "so-downgrade"],
)
def test_marca_e_downgrade_passam(source: str) -> None:
    assert unmarked_blocking_lines(source) == []


def test_marca_sem_motivo_nao_vale() -> None:
    source = 'def upgrade() -> None:\n    op.create_index("ix", "run", ["a"])  # lock-ok:\n'

    assert unmarked_blocking_lines(source) == [2]


def test_marca_de_outra_operacao_na_linha_acima_nao_cobre() -> None:
    source = (
        "def upgrade() -> None:\n"
        '    op.create_index("a", "run", ["a"])  # lock-ok: tabela nova\n'
        '    op.create_index("b", "run", ["b"])\n'
    )

    assert unmarked_blocking_lines(source) == [3]


@pytest.mark.parametrize(
    "operation",
    [
        'op.create_index("ix", "run", ["a"], postgresql_concurrently=True)',
        'op.drop_index("ix", table_name="run", postgresql_concurrently=True)',
        'op.execute("CREATE INDEX CONCURRENTLY ix ON run (a)")',
        'sql = "CREATE INDEX CONCURRENTLY ix ON run (a)"; op.execute(sql)',
        'op.execute("REINDEX (CONCURRENTLY) INDEX ix")',
        'op.execute("ALTER TABLE run DETACH PARTITION run_2026 CONCURRENTLY")',
    ],
)
def test_concurrently_fora_do_autocommit_e_recusado_mesmo_com_marca(operation: str) -> None:
    source = f"def upgrade() -> None:\n    {operation}  # lock-ok: tanto faz\n"

    assert concurrent_in_transaction_lines(source) == [2]


def test_concurrently_dentro_do_autocommit_passa() -> None:
    source = (
        "def upgrade() -> None:\n"
        "    with op.get_context().autocommit_block():\n"
        '        op.drop_index("ix", table_name="run", postgresql_concurrently=True)\n'
        '        op.create_index("ix", "run", ["a"], postgresql_concurrently=True)\n'
    )

    assert concurrent_in_transaction_lines(source) == []
    assert unmarked_blocking_lines(source) == []


def test_concurrently_junto_de_outra_instrucao_no_mesmo_execute_e_recusado() -> None:
    # Várias instruções numa mensagem só rodam numa transação implícita.
    source = (
        AUTOCOMMIT_HEADER
        + '        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_a;'
        + ' CREATE INDEX CONCURRENTLY ix_a ON run (a)")\n'
    )

    assert concurrent_in_transaction_lines(source) == [3]


def test_concurrently_com_ponto_e_virgula_no_fim_passa() -> None:
    source = AUTOCOMMIT_HEADER + '        op.execute("CREATE INDEX CONCURRENTLY ix ON run (a);")\n'

    assert concurrent_in_transaction_lines(source) == []


def test_sql_guardado_em_variavel_e_lido() -> None:
    source = (
        'INDEX_SQL = "CREATE INDEX ix_run_owner ON run (owner_id)"\n\n\n'
        "def upgrade() -> None:\n"
        "    op.execute(sa.text(INDEX_SQL))\n"
    )

    assert unmarked_blocking_lines(source) == [5]


def test_operacao_em_funcao_auxiliar_chamada_pelo_upgrade_e_lida() -> None:
    source = (
        "def upgrade() -> None:\n"
        "    _add_index()\n\n\n"
        "def _add_index() -> None:\n"
        '    op.create_index("ix_run_owner", "run", ["owner_id"])\n'
    )

    assert unmarked_blocking_lines(source) == [6]


def test_funcao_auxiliar_so_do_downgrade_fica_de_fora() -> None:
    source = (
        "def upgrade() -> None:\n"
        "    pass\n\n\n"
        "def downgrade() -> None:\n"
        "    _add_index()\n\n\n"
        "def _add_index() -> None:\n"
        '    op.create_index("ix_run_owner", "run", ["owner_id"])\n'
    )

    assert unmarked_blocking_lines(source) == []


def test_tabela_criada_no_mesmo_upgrade_dispensa_a_marca() -> None:
    # Ninguém lê nem escreve numa tabela que esta migration acabou de criar.
    source = (
        "def upgrade() -> None:\n"
        '    op.create_table("share", sa.Column("id", sa.BigInteger(), primary_key=True))\n'
        '    op.create_index("ix_share_id", "share", ["id"])\n'
        '    op.create_foreign_key("fk_share", "share", "run", ["run_id"], ["id"])\n'
        '    op.create_index("ix_run_owner", "run", ["owner_id"])\n'
    )

    assert unmarked_blocking_lines(source) == [5]


def test_sql_grande_e_analisado_em_tempo_linear() -> None:
    # Entrada que faria um regex com dois trechos variáveis voltar atrás em cada posição.
    sql = "ALTER TABLE run ADD a " * 2_000 + "DEFAULT 0"
    source = f'def upgrade() -> None:\n    op.execute("{sql}")\n'
    started = time.monotonic()

    assert unmarked_blocking_lines(source) == []
    assert time.monotonic() - started < MAX_ANALYSIS_S


def test_muitos_acrescimos_com_mais_igual_sao_lidos_em_sequencia() -> None:
    increments = '    sql += "ALTER COLUMN c SET DEFAULT 0, "\n' * 200
    source = (
        'def upgrade() -> None:\n    sql = "ALTER TABLE run "\n'
        + increments
        + '    sql += "ALTER COLUMN c SET NOT NULL"\n    op.execute(sql)\n'
    )
    started = time.monotonic()

    assert unmarked_blocking_lines(source) == [204]
    assert time.monotonic() - started < MAX_ANALYSIS_S


def test_valor_anterior_ao_mais_igual_continua_lido() -> None:
    source = (
        "def upgrade() -> None:\n"
        '    sql = "CREATE INDEX "\n'
        '    op.execute(sql + "ix_a ON run (a)")\n'
        '    sql += "CONCURRENTLY ix_b ON run (b)"\n'
    )

    assert unmarked_blocking_lines(source) == [3]


def test_coluna_guardada_em_variavel_e_lida() -> None:
    source = (
        "def upgrade() -> None:\n"
        '    column = sa.Column("key", sa.Text(), unique=True)\n'
        '    op.add_column("run", column)\n'
    )

    assert unmarked_blocking_lines(source) == [3]


def test_default_volatil_guardado_em_constante_e_lido() -> None:
    source = (
        'NEW_TOKEN = sa.text("gen_random_uuid()")\n\n\n'
        "def upgrade() -> None:\n"
        '    op.add_column("run", sa.Column("t", sa.Uuid(), server_default=NEW_TOKEN))\n'
    )

    assert unmarked_blocking_lines(source) == [5]


def test_variavel_do_downgrade_nao_contamina_o_upgrade() -> None:
    source = (
        "def upgrade() -> None:\n"
        '    sql = "UPDATE run SET size = 0 WHERE size IS NULL"\n'
        "    op.execute(sql)\n\n\n"
        "def downgrade() -> None:\n"
        '    sql = "CREATE INDEX ix_run_size ON run (size)"\n'
        "    op.execute(sql)\n"
    )

    assert unmarked_blocking_lines(source) == []


def test_tabela_criada_com_op_f_dispensa_a_marca() -> None:
    source = (
        "def upgrade() -> None:\n"
        '    op.create_table(op.f("share"), sa.Column("id", sa.BigInteger()))\n'
        '    op.create_index("ix_share_id", "share", ["id"])\n'
    )

    assert unmarked_blocking_lines(source) == []


AUTOCOMMIT_HEADER = "def upgrade() -> None:\n    with op.get_context().autocommit_block():\n"
CREATE_CONCURRENT = '        op.execute("CREATE INDEX CONCURRENTLY ix_a ON run (a)")\n'


@pytest.mark.parametrize(
    ("body", "line"),
    [
        ('        op.create_index("ix_a", "run", ["a"], postgresql_concurrently=True)\n', 3),
        ('        op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_a ON run (a)")\n', 3),
        ('        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_b")\n' + CREATE_CONCURRENT, 4),
        (CREATE_CONCURRENT + '        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_a")\n', 3),
        # No PostgreSQL, "IX_A" entre aspas é outro índice: ix_a continua sem limpeza.
        (
            "        op.execute('DROP INDEX CONCURRENTLY IF EXISTS \"IX_A\"')\n"
            + CREATE_CONCURRENT,
            4,
        ),
        ('        sql = "CREATE INDEX CONCURRENTLY ix_a ON run (a)"\n        op.execute(sql)\n', 4),
        (
            '        name = "ix_a"\n'
            '        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_b")\n'
            '        op.execute(f"CREATE INDEX CONCURRENTLY {name} ON run (a)")\n',
            5,
        ),
        (
            '        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {first()}")\n'
            '        op.execute(f"CREATE INDEX CONCURRENTLY {second()} ON run (a)")\n',
            4,
        ),
        (
            '        op.drop_index("Ix_A", "run", postgresql_concurrently=True, if_exists=True)\n'
            '        op.create_index("ix_a", "run", ["a"], postgresql_concurrently=True)\n',
            4,
        ),
        (
            "        op.execute('DROP INDEX CONCURRENTLY IF EXISTS \"my other\"')\n"
            "        op.execute('CREATE INDEX CONCURRENTLY \"my idx\" ON run (a)')\n",
            4,
        ),
        (
            "        op.execute('DROP INDEX CONCURRENTLY IF EXISTS \"a.b\"')\n"
            '        op.execute("CREATE INDEX CONCURRENTLY a.b ON run (a)")\n',
            4,
        ),
    ],
    ids=[
        "op-sem-drop",
        "sql-sem-drop",
        "drop-de-outro-indice",
        "drop-depois",
        "drop-com-aspas-e-outra-caixa",
        "sql-em-variavel",
        "f-string-com-outro-nome",
        "nomes-desconhecidos",
        "op-com-outra-caixa",
        "aspas-com-espaco-e-outro-nome",
        "ponto-entre-aspas-e-outro-schema",
    ],
)
def test_indice_concorrente_sem_drop_previo_e_recusado(body: str, line: int) -> None:
    assert concurrent_index_without_cleanup_lines(AUTOCOMMIT_HEADER + body) == [line]


@pytest.mark.parametrize(
    "body",
    [
        (
            '        op.drop_index("ix_a", "run", postgresql_concurrently=True, if_exists=True)\n'
            '        op.create_index("ix_a", "run", ["a"], postgresql_concurrently=True)\n'
        ),
        "        op.execute('DROP INDEX CONCURRENTLY IF EXISTS \"ix_a\"')\n" + CREATE_CONCURRENT,
        '        op.execute("DROP INDEX CONCURRENTLY IF EXISTS IX_A")\n' + CREATE_CONCURRENT,
        (
            '        name = "ix_a"\n'
            '        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")\n'
            '        op.execute(f"CREATE INDEX CONCURRENTLY {name} ON run (a)")\n'
        ),
        (
            '        op.drop_index("Ix_A", "run", postgresql_concurrently=True, if_exists=True)\n'
            '        op.create_index("Ix_A", "run", ["a"], postgresql_concurrently=True)\n'
        ),
        (
            '        op.execute("DROP INDEX CONCURRENTLY IF EXISTS IX_A")\n'
            '        op.create_index("ix_a", "run", ["a"], postgresql_concurrently=True)\n'
        ),
        (
            "        op.execute('DROP INDEX CONCURRENTLY IF EXISTS \"my idx\"')\n"
            "        op.execute('CREATE INDEX CONCURRENTLY \"my idx\" ON run (a)')\n"
        ),
        (
            "        op.execute('DROP INDEX CONCURRENTLY IF EXISTS public.\"ix_a\"')\n"
            '        op.execute("CREATE INDEX CONCURRENTLY PUBLIC.ix_a ON run (a)")\n'
        ),
    ],
    ids=[
        "op",
        "sql-com-aspas",
        "sql-sem-aspas-dobra-a-caixa",
        "f-string",
        "op-com-caixa",
        "sql-e-op",
        "aspas-com-espaco",
        "com-schema",
    ],
)
def test_indice_concorrente_com_drop_previo_passa(body: str) -> None:
    assert concurrent_index_without_cleanup_lines(AUTOCOMMIT_HEADER + body) == []
