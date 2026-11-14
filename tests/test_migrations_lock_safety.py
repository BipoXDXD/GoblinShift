"""Migration não segura lock que trava a API sem declarar por quê (ADR 057).

Vale para todas as migrations, inclusive as anteriores ao check, marcadas com o motivo de cada
operação. O `downgrade()` fica de fora, como no check de contract. As regras
estão em `tests/migration_lock_check.py`.
"""

import time
from pathlib import Path

import pytest
from pglast import parse_sql
from pglast.ast import AlterTableStmt

from tests.migration_lock_check import (
    REVISION_FILE,
    SAFE_OPERATIONS,
    checked_revisions,
    concurrent_in_transaction_lines,
    concurrent_index_without_cleanup_lines,
    unmarked_blocking_lines,
)
from tests.migration_sql import SAFE_ALTER_TABLE_COMMANDS, SAFE_STATEMENTS

VERSIONS = Path(__file__).resolve().parents[1] / "migrations" / "versions"
# Folga larga para a máquina do CI: o caso linear leva milissegundos.
MAX_ANALYSIS_S = 2.0


def test_migrations_so_bloqueiam_com_a_marca_lock_ok() -> None:
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


def test_check_cobre_todas_as_migrations() -> None:
    # As antigas também: as que travam levam `# lock-ok:` com o motivo de cada operação.
    names = ["0001_a.py", "0018_b.py", "0019_c.py", "README.md", "__init__.py"]

    assert checked_revisions(names) == ["0001_a.py", "0018_b.py", "0019_c.py"]


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
        # Na string de escape, `''` também é uma aspa dentro do texto.
        (
            "op.execute(\"ALTER TABLE run ALTER COLUMN c SET DEFAULT E'a''\\\\'b',"
            ' ALTER COLUMN c SET NOT NULL")'
        ),
        'op.execute(\'ALTER TABLE run ALTER COLUMN "a"" b" TYPE bigint\')',
        'op.execute("ALTER TABLE run DETACH PARTITION run_2027")',
        "op.execute('ALTER TABLE run DETACH PARTITION s.\"p 1\"')",
        'op.execute("ALTER TABLE run ADD CONSTRAINT nn NOT NULL c")',
        'op.execute("ALTER TABLE run ADD COLUMN c int, ADD c2 serial")',
        'op.execute("ALTER TABLE run ADD COLUMN c uuid DEFAULT public.gen_random_uuid()")',
        'op.execute("ALTER TABLE run ADD COLUMN c int DEFAULT coalesce(random()::int, 0)")',
        # Domínio: valida todas as colunas que o usam, com lock nas tabelas delas.
        'op.execute("ALTER DOMAIN d ADD CONSTRAINT k CHECK (VALUE > 0)")',
        'op.execute("ALTER DOMAIN d SET NOT NULL")',
        'op.execute("ALTER TABLE ALL IN TABLESPACE a SET TABLESPACE b")',
        'op.execute("ALTER TABLE run SET ACCESS METHOD heap2")',
        'op.execute("ALTER TABLE run ALTER c SET EXPRESSION AS (a + 1)")',
        'op.execute("CREATE SCHEMA s CREATE TABLE t (a int) CREATE INDEX i ON run (a)")',
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
        'op.execute("ALTER TABLE run DETACH PARTITION run_2027 CONCURRENTLY")',
        'op.execute("ALTER TABLE run DETACH PARTITION run_2027 FINALIZE")',
        "op.execute('ALTER TABLE run DETACH PARTITION s.\"p 1\" CONCURRENTLY')",
        "op.execute('ALTER TABLE run DETACH PARTITION \"s\".p FINALIZE')",
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
        "sql-detach-concorrente",
        "sql-detach-finalize",
        "sql-detach-qualificado-concorrente",
        "sql-detach-qualificado-finalize",
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
        "op.execute('ALTER TABLE run DETACH PARTITION s.\"p 1\" CONCURRENTLY')",
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


@pytest.mark.parametrize(
    "caller",
    ["def upgrade() -> None:\n    _add_index()\n", "passos = [_add_index]\n"],
    ids=["chamada-pelo-upgrade", "citada-no-modulo"],
)
def test_funcao_auxiliar_do_downgrade_tambem_usada_fora_dele_e_lida(caller: str) -> None:
    source = (
        "def downgrade() -> None:\n"
        "    _add_index()\n"
        "def _add_index() -> None:\n"
        '    op.create_index("ix_run_owner", "run", ["owner_id"])\n'
        f"{caller}"
    )

    assert unmarked_blocking_lines(source) == [4]


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
    sql = "ALTER TABLE run " + ", ".join(f"ADD a{i} int DEFAULT 0" for i in range(2_000))
    source = f'def upgrade() -> None:\n    op.execute("{sql}")\n'
    started = time.monotonic()

    assert unmarked_blocking_lines(source) == []
    assert time.monotonic() - started < MAX_ANALYSIS_S


@pytest.mark.parametrize(
    "operation",
    [
        # Repetição que faria um regex com dois trechos variáveis voltar atrás em cada posição.
        'op.execute("' + "ALTER TABLE run ADD a " * 2_000 + 'DEFAULT 0")',
        'op.execute(f"ALTER TABLE run {acao}")',
        'op.execute("ALTER TABLE run ADD COLUMN c int DEFAULT {}()".format(funcao))',
        'op.execute(f"ALTER TABLE run ADD COLUMN c {tipo}")',
        'op.execute(f"ALTER TABLE run ADD COLUMN c int DEFAULT {expr}")',
        'op.execute("SELECT \'x")',
    ],
    ids=[
        "sql-invalido-longo",
        "palavra-chave-desconhecida",
        "funcao-desconhecida-no-default",
        "tipo-desconhecido",
        "default-desconhecido",
        "aspas-sem-fechar",
    ],
)
def test_sql_que_o_check_nao_entende_pede_a_marca(operation: str) -> None:
    source = f"def upgrade() -> None:\n    {operation}\n"
    started = time.monotonic()

    assert unmarked_blocking_lines(source) == [2]
    assert time.monotonic() - started < MAX_ANALYSIS_S


def test_concurrently_em_sql_que_o_parser_recusa_continua_valendo() -> None:
    create = 'op.execute(f"CREATE INDEX CONCURRENTLY ix ON run {cols}")\n'

    assert concurrent_in_transaction_lines(f"def upgrade() -> None:\n    {create}") == [2]
    # Sem nome resolvido, nenhum DROP anterior conta como limpeza.
    assert concurrent_index_without_cleanup_lines(AUTOCOMMIT_HEADER + f"        {create}") == [3]


def test_texto_que_nem_o_scanner_aceita_conta_como_concorrente() -> None:
    source = 'def upgrade() -> None:\n    op.execute("SELECT \'x")\n'

    assert concurrent_in_transaction_lines(source) == [2]


def test_refresh_concorrente_que_o_parser_recusa_roda_em_transacao() -> None:
    # O CONCURRENTLY do REFRESH MATERIALIZED VIEW não exige autocommit, parseando ou não.
    refresh = 'op.execute(f"REFRESH MATERIALIZED VIEW CONCURRENTLY mv {opcao}")'
    source = f"def upgrade() -> None:\n    {refresh}\n"

    assert unmarked_blocking_lines(source) == [2]
    assert concurrent_in_transaction_lines(source) == []


@pytest.mark.parametrize(
    "operation",
    [
        'op.execute("ALTER TABLE run ADD CONSTRAINT nn NOT NULL c NOT VALID")',
        # No PostgreSQL 18 a coluna gerada sem STORED é VIRTUAL: não grava nada.
        'op.execute("ALTER TABLE run ADD COLUMN t int GENERATED ALWAYS AS (a)")',
        'op.execute("VACUUM (FULL false) run")',
        "op.execute(\"SELECT 'ALTER TABLE run ALTER c SET NOT NULL' /* CLUSTER run */\")",
    ],
    ids=["not-null-not-valid", "gerada-sem-stored", "vacuum-full-false", "texto"],
)
def test_forma_segura_lida_pelo_parser_passa(operation: str) -> None:
    source = f"def upgrade() -> None:\n    {operation}\n"

    assert unmarked_blocking_lines(source) == []


# Um caso por entrada da allowlist (negar por padrão): o tipo de nó, a forma segura.
SAFE_SQL_BY_NODE = [
    ("SelectStmt", "SELECT count(*) FROM run"),
    ("InsertStmt", "INSERT INTO run_status (name) VALUES ('paused')"),
    ("UpdateStmt", "UPDATE run SET size = 0 WHERE size IS NULL"),
    ("DeleteStmt", "DELETE FROM run_status WHERE name = 'old'"),
    ("CommentStmt", "COMMENT ON COLUMN run.size IS 'bytes'"),
    ("CreateStmt", "CREATE TABLE run_tag (run_id bigint REFERENCES run (id), tag text)"),
    ("CreateSchemaStmt", "CREATE SCHEMA audit CREATE TABLE entry (id bigint)"),
    ("IndexStmt", "CREATE INDEX CONCURRENTLY ix ON run (a)"),
    ("DropStmt", "DROP INDEX CONCURRENTLY IF EXISTS ix"),
    ("ReindexStmt", "REINDEX INDEX CONCURRENTLY ix"),
    ("RefreshMatViewStmt", "REFRESH MATERIALIZED VIEW CONCURRENTLY run_summary"),
    ("VacuumStmt", "ANALYZE run"),
    ("AlterEnumStmt", "ALTER TYPE run_status ADD VALUE IF NOT EXISTS 'paused'"),
    ("AlterTableStmt", "ALTER TABLE run ADD COLUMN note text"),
]
SAFE_ALTER_TABLE_BY_COMMAND = [
    ("AT_AddColumn", "ALTER TABLE run ADD COLUMN on_hold boolean DEFAULT false"),
    ("AT_DropColumn", "ALTER TABLE run DROP COLUMN IF EXISTS legacy"),
    ("AT_ColumnDefault", "ALTER TABLE run ALTER COLUMN status SET DEFAULT 'new'"),
    ("AT_DropNotNull", "ALTER TABLE run ALTER COLUMN status DROP NOT NULL"),
    ("AT_AddConstraint", "ALTER TABLE run ADD CONSTRAINT ck CHECK (size > 0) NOT VALID"),
    ("AT_ValidateConstraint", "ALTER TABLE run VALIDATE CONSTRAINT ck"),
    ("AT_DropConstraint", "ALTER TABLE run DROP CONSTRAINT IF EXISTS ck"),
    ("AT_DetachPartition", "ALTER TABLE run DETACH PARTITION run_2027 CONCURRENTLY"),
    ("AT_DetachPartitionFinalize", "ALTER TABLE run DETACH PARTITION run_2027 FINALIZE"),
]
SAFE_OPERATION_CASES = [
    ("create_table", 'op.create_table("run_tag", sa.Column("tag", sa.Text()))'),
    ("create_index", 'op.create_index("ix", "run", ["a"], postgresql_concurrently=True)'),
    ("drop_index", 'op.drop_index("ix", "run", postgresql_concurrently=True, if_exists=True)'),
    ("add_column", 'op.add_column("run", sa.Column("note", sa.Text()))'),
    ("drop_column", 'op.drop_column("run", "legacy")'),
    ("alter_column", 'op.alter_column("run", "status", server_default="new", comment="fase")'),
    (
        "create_foreign_key",
        'op.create_foreign_key("fk", "run", "account", ["o"], ["id"], postgresql_not_valid=True)',
    ),
    (
        "create_check_constraint",
        'op.create_check_constraint("ck", "run", "size > 0", postgresql_not_valid=True)',
    ),
    ("drop_constraint", 'op.drop_constraint("ck", "run", type_="check")'),
    ("bulk_insert", 'op.bulk_insert(run_status, [{"name": "paused"}])'),
    ("execute", 'op.execute("UPDATE run SET size = 0 WHERE size IS NULL")'),
    ("exec_driver_sql", 'op.get_bind().exec_driver_sql("SELECT 1")'),
    ("f", 'op.f("ix_run_note")'),
    ("get_bind", "op.get_bind()"),
    ("get_context", "op.get_context()"),
    ("inline_literal", 'op.inline_literal("paused")'),
    ("autocommit_block", "op.get_context().autocommit_block()"),
    ("cursor", "op.get_bind().connection.cursor()"),
]


@pytest.mark.parametrize(("node", "sql"), SAFE_SQL_BY_NODE, ids=[n for n, _ in SAFE_SQL_BY_NODE])
def test_cada_no_da_allowlist_de_sql_passa_sem_marca(node: str, sql: str) -> None:
    (statement,) = parse_sql(sql)
    source = f"def upgrade() -> None:\n    op.execute({sql!r})\n"

    assert type(statement.stmt).__name__ == node
    assert unmarked_blocking_lines(source) == []


@pytest.mark.parametrize(
    ("command", "sql"), SAFE_ALTER_TABLE_BY_COMMAND, ids=[c for c, _ in SAFE_ALTER_TABLE_BY_COMMAND]
)
def test_cada_subcomando_seguro_de_alter_table_passa_sem_marca(command: str, sql: str) -> None:
    (statement,) = parse_sql(sql)
    source = f"def upgrade() -> None:\n    op.execute({sql!r})\n"

    assert isinstance(statement.stmt, AlterTableStmt)
    assert [cmd.subtype.name for cmd in statement.stmt.cmds or ()] == [command]
    assert unmarked_blocking_lines(source) == []


@pytest.mark.parametrize(
    ("name", "operation"), SAFE_OPERATION_CASES, ids=[n for n, _ in SAFE_OPERATION_CASES]
)
def test_cada_operacao_da_allowlist_passa_sem_marca(name: str, operation: str) -> None:
    source = f"def upgrade() -> None:\n    {operation}\n"

    assert f".{name}(" in operation
    assert unmarked_blocking_lines(source) == []


def test_allowlist_tem_um_caso_por_entrada() -> None:
    # Entrada nova na allowlist sem caso aqui reprova: cada uma precisa de uma forma segura testada.
    assert {node for node, _ in SAFE_SQL_BY_NODE} == {kind.__name__ for kind in SAFE_STATEMENTS}
    assert {command for command, _ in SAFE_ALTER_TABLE_BY_COMMAND} == {
        subtype.name for subtype in SAFE_ALTER_TABLE_COMMANDS
    }
    assert {name for name, _ in SAFE_OPERATION_CASES} == set(SAFE_OPERATIONS)


@pytest.mark.parametrize(
    "sql",
    [
        "CREATE TRIGGER t BEFORE UPDATE ON run FOR EACH ROW EXECUTE FUNCTION touch()",
        "CREATE FUNCTION touch() RETURNS trigger LANGUAGE plpgsql AS 'BEGIN RETURN NEW; END'",
        # Desligaria a proteção do lock_timeout para o resto da migration.
        "SET lock_timeout = 0",
        "DO $$BEGIN CREATE INDEX ix ON run (a); END$$",
        "DROP TABLE run",
        "ALTER TABLE run RENAME TO runs",
        "ALTER TABLE run ENABLE ROW LEVEL SECURITY",
        "ALTER DOMAIN d ADD CONSTRAINT k CHECK (VALUE > 0) NOT VALID",
        "CREATE TABLE run_2028 PARTITION OF run FOR VALUES IN (2028)",
        "CREATE TABLE run_child () INHERITS (run)",
        "GRANT SELECT ON run TO reader",
        "ALTER TYPE run_status RENAME VALUE 'old' TO 'new'",
        "vacuum_full_custom run",
        "SELECT 1; __unresolved__ x; SELECT 2",
    ],
    ids=[
        "trigger",
        "funcao",
        "set",
        "do",
        "drop-table",
        "rename",
        "subcomando-desconhecido",
        "dominio",
        "partition-of",
        "inherits",
        "grant",
        "enum-rename",
        "sem-palavra-chave",
        "trecho-sem-palavra-chave-no-meio",
    ],
)
def test_no_de_sql_fora_da_allowlist_pede_a_marca(sql: str) -> None:
    unmarked = f"def upgrade() -> None:\n    op.execute({sql!r})\n"
    marked = f"def upgrade() -> None:\n    op.execute({sql!r})  # lock-ok: revisado\n"

    assert unmarked_blocking_lines(unmarked) == [2]
    assert unmarked_blocking_lines(marked) == []


@pytest.mark.parametrize(
    "operation",
    [
        'op.rename_table("run", "runs")',
        'op.drop_table("run")',
        'op.batch_alter_table("run")',
        'op.drop_index("ix", "run")',
        'op.alter_column("run", "status", new_column_name="state")',
        'op.alter_column("run", "status", nullable=True, autoincrement=True)',
        'op.alter_column("run", "status", nullable=flag)',
        'op.create_table_comment("run", "execuções")',
    ],
    ids=[
        "rename-table",
        "drop-table",
        "batch",
        "drop-index-sem-concurrently",
        "alter-column-rename",
        "alter-column-opcao-desconhecida",
        "alter-column-nullable-variavel",
        "operacao-desconhecida",
    ],
)
def test_operacao_fora_da_allowlist_pede_a_marca(operation: str) -> None:
    source = f"def upgrade() -> None:\n    {operation}\n"

    assert unmarked_blocking_lines(source) == [2]


@pytest.mark.parametrize(
    ("header", "call"),
    [
        ("from alembic.op import rename_table as rename", 'rename("run", "runs")'),
        ("from alembic import op as ops", 'ops.rename_table("run", "runs")'),
        ("import alembic.op as o", 'o.rename_table("run", "runs")'),
        ("from alembic.op import *", 'rename_table("run", "runs")'),
    ],
    ids=["importada-com-apelido", "apelido-do-op", "import-do-modulo", "import-estrela"],
)
def test_operacao_chamada_por_outro_nome_tambem_e_conferida(header: str, call: str) -> None:
    source = f"{header}\n\n\ndef upgrade() -> None:\n    {call}\n"

    assert unmarked_blocking_lines(source) == [5]


def test_chamada_que_nao_e_do_alembic_nao_pede_a_marca() -> None:
    source = (
        "def upgrade() -> None:\n"
        '    rotulo = "a".upper()\n'
        '    op.add_column("run", sa.Column(rotulo, sa.Text(), nullable=True))\n'
    )

    assert unmarked_blocking_lines(source) == []


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


def test_alternativas_alem_do_teto_contam_como_bloqueantes() -> None:
    # Cada atribuição é uma alternativa; a 17ª, a bloqueante, passa do teto de leitura.
    source = (
        "def upgrade() -> None:\n"
        + '    sql = "SELECT 1"\n' * 16
        + '    sql = "ALTER TABLE run ALTER COLUMN c SET NOT NULL"\n'
        + "    op.execute(sql)\n"
    )

    assert unmarked_blocking_lines(source) == [19]


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
        (
            '        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_a")\n'
            '        op.execute("CREATE INDEX CONCURRENTLY ix_a ON s.run (a)")\n',
            4,
        ),
        (
            '        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ÍX_A")\n'
            "        op.execute('CREATE INDEX CONCURRENTLY \"íx_a\" ON run (a)')\n",
            4,
        ),
        (
            '        op.execute("CREATE INDEX CONCURRENTLY ON run (a)")\n',
            3,
        ),  # Sem IF EXISTS, o DROP falha no primeiro deploy, quando o índice ainda não existe.
        ('        op.execute("DROP INDEX CONCURRENTLY ix_a")\n' + CREATE_CONCURRENT, 4),
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
        # O índice fica no schema da tabela; o DROP sem schema pode mirar outro.
        "drop-sem-schema-e-tabela-de-outro-schema",
        # O PostgreSQL só dobra a caixa de A a Z.
        "caixa-nao-ascii",
        "sem-nome",
        "drop-sem-if-exists",
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
            '        op.execute("CREATE INDEX CONCURRENTLY ix_a ON PUBLIC.run (a)")\n'
        ),
        (
            "        op.drop_index(\n"
            '            "ix_a", "run", schema="s", postgresql_concurrently=True, if_exists=True\n'
            "        )\n"
            "        op.create_index(\n"
            '            "ix_a", "run", ["a"], schema="s", postgresql_concurrently=True\n'
            "        )\n"
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
        "schema-da-tabela",
        "op-com-schema",
    ],
)
def test_indice_concorrente_com_drop_previo_passa(body: str) -> None:
    assert concurrent_index_without_cleanup_lines(AUTOCOMMIT_HEADER + body) == []


@pytest.mark.parametrize(
    ("header", "body", "line"),
    [
        ("import alembic", 'alembic.op.create_index("ix", "run", ["a"])', 5),
        ("from alembic import op", 'o = op\n    o.create_index("ix", "run", ["a"])', 6),
        ("from alembic import op", 'getattr(op, "create_index")("ix", "run", ["a"])', 5),
    ],
    ids=["caminho-completo", "op-em-variavel", "getattr"],
)
def test_receptor_desconhecido_da_operacao_nao_esconde_a_chamada(
    header: str, body: str, line: int
) -> None:
    source = f"{header}\n\n\ndef upgrade() -> None:\n    {body}\n"

    assert unmarked_blocking_lines(source) == [line]


@pytest.mark.parametrize(
    ("body", "line"),
    [
        ('U = True\n    op.add_column("run", sa.Column("k", sa.Text(), unique=U))', 3),
        ('op.add_column("run", sa.Column("k", sa.Text(), **opcoes))', 2),
        ('op.add_column("run", sa.Column("k", sa.Text(), autoincrement=True))', 2),
        ('op.add_column("run", sa.Column("k", sa.Text(), nullable=flag))', 2),
        ('op.add_column("run", _coluna())', 2),
        ('op.add_column("run", sa.Column("k", postgresql.DOMAIN("d", sa.Text())))', 2),
        ('op.add_column("run", sa.Column("k", _tipo()))', 2),
        ('op.add_column("run", sa.Column("k", sa.Text()), **opcoes)', 2),
    ],
    ids=[
        "unique-por-variavel",
        "kwargs",
        "opcao-desconhecida",
        "nullable-variavel",
        "coluna-de-funcao",
        "dominio",
        "tipo-de-funcao",
        "kwargs-no-add-column",
    ],
)
def test_add_column_so_passa_com_coluna_literal_e_opcoes_conhecidas(body: str, line: int) -> None:
    source = f"def upgrade() -> None:\n    {body}\n"

    assert unmarked_blocking_lines(source) == [line]


@pytest.mark.parametrize(
    "default",
    [
        'sa.text("random_normal()")',
        'sa.text("txid_current()")',
        'sa.text("my_volatile()")',
        "sa.func.my_volatile()",
        "valor_externo",
        'sa.text("now() + (SELECT 1)")',
    ],
    ids=["random-normal", "txid", "funcao-do-projeto", "sa-func", "variavel", "subconsulta"],
)
def test_default_de_coluna_nova_fora_da_allowlist_pede_a_marca(default: str) -> None:
    source = (
        "def upgrade() -> None:\n"
        f'    op.add_column("run", sa.Column("x", sa.Text(), server_default={default}))\n'
    )

    assert unmarked_blocking_lines(source) == [2]


@pytest.mark.parametrize(
    "default",
    [
        '"novo"',
        'sa.text("0")',
        "sa.text(\"'{}'::jsonb\")",
        'sa.text("CURRENT_TIMESTAMP")',
        "sa.func.now()",
        "sa.true()",
    ],
    ids=["texto", "numero", "cast", "current-timestamp", "sa-func-now", "sa-true"],
)
def test_default_constante_ou_estavel_passa(default: str) -> None:
    source = (
        "def upgrade() -> None:\n"
        f'    op.add_column("run", sa.Column("x", sa.Text(), server_default={default}))\n'
    )

    assert unmarked_blocking_lines(source) == []


@pytest.mark.parametrize(
    "sql",
    [
        "ALTER TABLE run ADD COLUMN x float8 DEFAULT random_normal()",
        "ALTER TABLE run ADD COLUMN x bigint DEFAULT txid_current()",
        "ALTER TABLE run ADD COLUMN x text DEFAULT my_volatile()",
        "ALTER TABLE run ADD COLUMN x positive_int",
        "ALTER TABLE run ADD COLUMN x s.dominio DEFAULT 1",
    ],
    ids=["random-normal", "txid", "funcao-do-projeto", "dominio", "dominio-qualificado"],
)
def test_coluna_nova_em_sql_fora_da_allowlist_pede_a_marca(sql: str) -> None:
    source = f'def upgrade() -> None:\n    op.execute("{sql}")\n'

    assert unmarked_blocking_lines(source) == [2]


@pytest.mark.parametrize(
    "sql",
    [
        "ALTER TABLE run ADD COLUMN x int DEFAULT -1",
        "ALTER TABLE run ADD COLUMN x varchar(10) DEFAULT 'a'",
        "ALTER TABLE run ADD COLUMN x timestamptz DEFAULT now()",
        "ALTER TABLE run ADD COLUMN x timestamp with time zone DEFAULT CURRENT_TIMESTAMP",
        "ALTER TABLE run ADD COLUMN x jsonb DEFAULT '{}'::jsonb",
        "ALTER TABLE run ADD COLUMN x uuid",
        "ALTER TABLE run ADD COLUMN x text[] DEFAULT ARRAY[]::text[]",
    ],
    ids=["negativo", "varchar", "now", "current-timestamp", "cast", "uuid", "array"],
)
def test_coluna_nova_em_sql_com_tipo_nativo_e_default_estavel_passa(sql: str) -> None:
    source = f'def upgrade() -> None:\n    op.execute("{sql}")\n'

    assert unmarked_blocking_lines(source) == []


@pytest.mark.parametrize(
    "create",
    [
        'op.create_table("run", sa.Column("a", sa.Text()), if_not_exists=True)',
        'op.create_table("run", sa.Column("a", sa.Text()), schema="arquivo")',
    ],
    ids=["if-not-exists", "outro-schema"],
)
def test_tabela_que_pode_ja_existir_nao_dispensa_a_marca(create: str) -> None:
    source = f'def upgrade() -> None:\n    {create}\n    op.create_index("ix", "run", ["a"])\n'

    assert unmarked_blocking_lines(source) == [3]


def test_operacao_em_outro_schema_nao_e_a_tabela_criada() -> None:
    # `arquivo.run` já existe e tem dados; só o `run` do schema padrão nasceu neste upgrade.
    source = (
        "def upgrade() -> None:\n"
        '    op.create_table("run", sa.Column("a", sa.Text()))\n'
        '    op.create_index("ix", "run", ["a"], schema="arquivo")\n'
    )

    assert unmarked_blocking_lines(source) == [3]


def test_operacao_dentro_do_batch_e_conferida_mesmo_com_a_marca_no_with() -> None:
    source = (
        "def upgrade() -> None:\n"
        '    with op.batch_alter_table("run") as batch:  # lock-ok: rename barato\n'
        '        batch.create_index("ix", ["a"])\n'
    )

    assert unmarked_blocking_lines(source) == [3]


def test_metodo_de_texto_desconhecido_nao_e_lido_como_o_sql() -> None:
    source = (
        "def upgrade() -> None:\n"
        '    sql = "ALTER TABLE run ALTER COLUMN c SET NOT NULL"\n'
        '    op.execute(sql.replace("SELECT 1", "SELECT 2"))\n'
    )

    assert unmarked_blocking_lines(source) == [3]


@pytest.mark.parametrize(
    ("source", "line"),
    [
        ('def upgrade() -> None:\n    op.execute(**{"sqltext": "CREATE INDEX i ON run (a)"})\n', 2),
        ("def upgrade() -> None:\n    op.execute()\n", 2),
        (
            "def upgrade() -> None:\n"
            '    op.execute("SELECT {}".format("1; CREATE INDEX i ON run (a)"))\n',
            2,
        ),
        (
            "def upgrade() -> None:\n"
            '    op.execute("SELECT %s" % "1; CREATE INDEX i ON run (a)")\n',
            2,
        ),
        (
            "def _valor() -> str:\n"
            '    return "1; CREATE INDEX i ON run (a)"\n'
            "def upgrade() -> None:\n"
            '    op.execute(f"SELECT {_valor()}")\n',
            4,
        ),
        (
            'sql = "SELECT 1"\n'
            "def upgrade() -> None:\n"
            '    for sql in ["CREATE INDEX i ON run (a)"]:\n'
            "        op.execute(sql)\n",
            4,
        ),
        (
            'sql = "SELECT 1"\n'
            "def _run(sql: str) -> None:\n"
            "    op.execute(sql)\n"
            "def upgrade() -> None:\n"
            '    _run("CREATE INDEX i ON run (a)")\n',
            3,
        ),
        (
            "def upgrade() -> None:\n"
            "    cur = op.get_bind().connection.cursor()\n"
            '    cur.executemany("CREATE INDEX i ON run (a)", [()])\n',
            3,
        ),
        (
            "def upgrade() -> None:\n"
            '    op.get_context().impl._exec(sa.text("CREATE INDEX i ON run (a)"))\n',
            2,
        ),
        (
            "def _rodar(conn: object) -> None:\n"
            '    conn.executemany("CREATE INDEX i ON run (a)", [()])\n',
            2,
        ),
        (
            "def upgrade() -> None:\n"
            "    cur = op.get_bind().connection.cursor()\n"
            '    cur.copy_expert("COPY run FROM STDIN", arquivo)\n',
            3,
        ),
        (
            "def upgrade() -> None:\n"
            "    op.execute(\"ALTER TABLE run ADD COLUMN c text DEFAULT '{}'\".format(rotulo))\n",
            2,
        ),
        ('def _real() -> None:\n    op.create_index("i", "run", ["a"])\nupgrade = _real\n', 2),
        (
            'criar = lambda: op.create_index("i", "run", ["a"])\n'
            "def upgrade() -> None:\n"
            "    criar()\n",
            1,
        ),
        (
            "class Passos:\n"
            "    @staticmethod\n"
            "    def indice() -> None:\n"
            '        op.create_index("i", "run", ["a"])\n'
            "def upgrade() -> None:\n"
            "    Passos.indice()\n",
            4,
        ),
    ],
    ids=[
        "kwargs-no-execute",
        "execute-sem-sql",
        "format-com-literal",
        "percent-com-literal",
        "fstring-com-chamada",
        "for-religa-o-nome",
        "parametro-religa-o-nome",
        "executemany-do-cursor",
        "metodo-privado-do-contexto",
        "executemany-de-parametro",
        "outro-metodo-do-cursor",
        "format-entre-aspas",
        "upgrade-apelido",
        "lambda-do-modulo",
        "metodo-de-classe",
    ],
)
def test_operacao_que_o_check_nao_resolve_pede_a_marca(source: str, line: int) -> None:
    # O check lê o módulo inteiro, salvo o downgrade(); valor que ele não resolve com certeza
    # (parâmetro, laço, chamada) conta como desconhecido, e SQL desconhecido pede a marca.
    assert unmarked_blocking_lines(source) == [line]


@pytest.mark.parametrize(
    "binding",
    [
        "    with abrir() as sql:\n        op.execute(sql)\n",
        "    try:\n        ler()\n    except Exception as sql:\n        op.execute(sql)\n",
        "    if sql := ler():\n        op.execute(sql)\n",
        "    sql, _ = ler()\n    op.execute(sql)\n",
        "    from textos import sql\n    op.execute(sql)\n",
        "    import textos as sql\n    op.execute(sql)\n",
        "    match ler():\n        case sql:\n            op.execute(sql)\n",
        "    match ler():\n        case [*sql]:\n            op.execute(sql)\n",
        "    _ = [op.execute(sql) for sql in ler()]\n",
    ],
    ids=[
        "with",
        "except",
        "walrus",
        "desempacotamento",
        "from-import",
        "import-como",
        "match-captura",
        "match-estrela",
        "compreensao",
    ],
)
def test_nome_religado_de_outra_forma_conta_como_desconhecido(binding: str) -> None:
    # A atribuição do módulo não vale mais depois que o nome é ligado de novo. O execute fica
    # sempre na última linha.
    source = f'sql = "SELECT 1"\ndef upgrade() -> None:\n{binding}'

    assert unmarked_blocking_lines(source) == [source.count("\n")]


@pytest.mark.parametrize(
    ("source", "lines"),
    [
        (
            'OPCOES = {"if_not_exists": True}\n'
            "def upgrade() -> None:\n"
            '    op.create_table("run", sa.Column("a", sa.Text()), **OPCOES)\n'
            '    op.create_index("i", "run", ["a"])\n',
            [3, 4],
        ),
        (
            "def upgrade() -> None:\n"
            "    if FLAG:\n"
            '        op.create_table("run", sa.Column("a", sa.Text()))\n'
            '    op.create_index("i", "run", ["a"])\n',
            [4],
        ),
        (
            "def _criar() -> None:\n"
            '    op.create_table("run", sa.Column("a", sa.Text()))\n'
            "def upgrade() -> None:\n"
            "    _criar()\n"
            '    op.create_index("i", "run", ["a"])\n',
            [5],
        ),
        (
            "def upgrade() -> None:\n"
            '    op.create_index("i", "run", ["a"])\n'
            '    op.create_table("run", sa.Column("a", sa.Text()))\n',
            [2],
        ),
        (
            "def upgrade() -> None:\n"
            '    op.create_table("filho", sa.Column("a", sa.Text()), postgresql_inherits="run")\n',
            [2],
        ),
    ],
    ids=["kwargs", "ramo-condicional", "outra-funcao", "depois-da-operacao", "inherits"],
)
def test_tabela_criada_so_dispensa_a_marca_quando_e_certo_que_e_nova(
    source: str, lines: list[int]
) -> None:
    # Com `**`, o próprio create_table pede a marca: as opções escondidas podem herdar de outra.
    assert unmarked_blocking_lines(source) == lines


def test_tabela_criada_na_mesma_funcao_auxiliar_dispensa_a_marca() -> None:
    source = (
        "def _criar() -> None:\n"
        '    op.create_table("run_tag", sa.Column("a", sa.Text()))\n'
        '    op.create_index("i", "run_tag", ["a"])\n'
        "def upgrade() -> None:\n"
        "    _criar()\n"
    )

    assert unmarked_blocking_lines(source) == []


@pytest.mark.parametrize(
    "column",
    [
        'sa.Column("x", sa.Boolean(create_constraint=True), nullable=True)',
        'sa.Column("x", sa.Enum("a", "b", native_enum=False, create_constraint=True))',
        'sa.Column("x", Dominio())',
        'sa.Column("x", sa.ARRAY(Dominio()))',
        'sa.Column("x", sa.Numeric(**PRECISAO))',
        'sa.Column("x", Fabrica())',
        'sa.Column("x", fabrica())',
    ],
    ids=[
        "boolean-com-check",
        "enum-com-check",
        "tipo-local",
        "tipo-local-aninhado",
        "kwargs",
        "funcao-local",
        "funcao-importada",
    ],
)
def test_tipo_que_cria_check_ou_e_do_projeto_pede_a_marca(column: str) -> None:
    source = (
        "from textos import fabrica\n"
        "class Dominio(sa.types.UserDefinedType):\n"
        "    def get_col_spec(self) -> str:\n"
        '        return "meu_dominio"\n'
        "def Fabrica() -> object:\n"
        "    return Dominio()\n"
        "def upgrade() -> None:\n"
        f'    op.add_column("run", {column})\n'
    )

    assert unmarked_blocking_lines(source) == [8]


@pytest.mark.parametrize(
    "column",
    [
        'sa.Column("x", sa.Enum("a", "b", name="fase"))',
        'sa.Column("x", sa.Boolean(create_constraint=False))',
        'sa.Column("x", postgresql.JSONB())',
        'sa.Column("x", sa.ARRAY(sa.Text()))',
        'sa.Column("x", Text())',
    ],
    ids=["enum-nativo", "boolean-sem-check", "jsonb", "array", "importado-solto"],
)
def test_tipo_do_sqlalchemy_sem_check_passa(column: str) -> None:
    source = (
        "import sqlalchemy as sa\n"
        "from sqlalchemy import Text\n"
        "from sqlalchemy.dialects import postgresql\n"
        f'def upgrade() -> None:\n    op.add_column("run", {column})\n'
    )

    assert unmarked_blocking_lines(source) == []
