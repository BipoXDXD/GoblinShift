"""Leitura estática das migrations atrás de DDL que trava a API (ADR 057).

DDL que reescreve ou varre a tabela sob ACCESS EXCLUSIVE ou SHARE (índice sem CONCURRENTLY,
constraint validada na hora, SET NOT NULL, troca de tipo, coluna com default volátil, única,
indexada, com FK ou CHECK) para as escritas, e às vezes as leituras, enquanto roda.

Modelo de ameaça: o check protege contra DDL bloqueante acidental, escrito por um autor honesto, e
não contra ofuscação deliberada; para essa, vale a revisão do PR.

O check nega por padrão, no nível da chamada, com uma allowlist mínima: toda chamada no módulo
lido pede a marca, salvo
- a operação do Alembic de `SAFE_OPERATIONS`, na forma exata que a condição dela reconhece:
  CREATE TABLE, CREATE INDEX CONCURRENTLY, ADD COLUMN anulável sem default e `op.execute` de SQL
  literal que `tests/migration_sql.py` aceita (as mesmas três instruções);
- um construtor do SQLAlchemy de `INERT_SQLALCHEMY` (`sa.Column`, `sa.Text()`...) ou `sa.func.x`,
  que só montam objetos em memória;
- uma função do próprio módulo, cujo corpo também é lido.
Qualquer outra operação, opção, cláusula ou forma (variável no lugar do literal, f-string, `**`,
`op.get_bind()`, `map`, decorator, classe com base, `with` que não é `autocommit_block`) pede a
marca, mesmo que não trave nada: é melhor um falso positivo que pede `# lock-ok:` do que um falso
negativo. Quem precisa dela marca `# lock-ok: <motivo>` na linha da operação ou na linha só de
comentário logo acima. Operação sobre uma tabela que a mesma função acabou de criar não trava
ninguém e dispensa a marca.

Numa revisão com `autocommit_block()`, o retry do `lock_timeout` repete a revisão inteira: nela
não pode haver mudança de schema (`schema_change_beside_autocommit_lines`), só dados e, dentro do
bloco, o par `DROP INDEX CONCURRENTLY IF EXISTS` / CREATE INDEX CONCURRENTLY.

`op` e `sa` só valem pelo import (`from alembic import op`, `import sqlalchemy as sa`) quando o
nome não é ligado de nenhuma outra forma no módulo nem tem atributo atribuído; `import *` anula
todos. O check lê o módulo inteiro, salvo as funções que só o `downgrade()` alcança. Continua
estático: código de outro módulo (o que um import executa, um objeto importado que roda código ao
ser lido, iterado ou somado) escapa, e a revisão do PR vale.
"""

import ast
import re
from collections import Counter
from collections.abc import Callable, Iterator, Mapping

from tests.migration_source import comments, is_marked
from tests.migration_sql import PLACEHOLDER, IndexName, analyze_sql, only_changes_rows

REVISION_FILE = re.compile(r"^(\d{4})_\w+\.py$")
LOCK_OK_MARK = re.compile(r"#\s*lock-ok:\s*\S")

ALEMBIC_OP = "alembic.op"
SQLALCHEMY = "sqlalchemy"
# Módulos do SQLAlchemy de onde vêm os construtores conhecidos.
SQLALCHEMY_MODULES = frozenset(
    {SQLALCHEMY, "sqlalchemy.types", "sqlalchemy.schema", "sqlalchemy.dialects.postgresql"}
)
# `sa.func.qualquer(...)` só monta a expressão da função SQL; quem a usa decide o resto.
SQLALCHEMY_FUNC = "sqlalchemy.func."
# Tipos de coluna nova que não criam CHECK, domínio nem sequência. `Boolean` e `Enum` não nativo
# criam CHECK com `create_constraint=True`, que não está entre as opções aceitas.
SAFE_COLUMN_TYPES = frozenset(
    {
        "Text", "String", "Unicode", "UnicodeText", "Integer", "BigInteger", "SmallInteger",
        "Boolean", "DateTime", "Date", "Time", "Interval", "Numeric", "Float", "LargeBinary",
        "JSON", "Uuid", "UUID", "JSONB", "CIDR", "INET", "TIMESTAMP", "ARRAY",
    }
)  # fmt: skip
ARRAY_TYPE = "ARRAY"
# Opções de tipo que só mudam o tipo nativo escolhido, sempre com valor literal.
SAFE_TYPE_OPTIONS = frozenset(
    {"length", "timezone", "precision", "scale", "asdecimal", "as_uuid", "none_as_null"}
    | {"dimensions"}
)
# Construtores que só montam objetos em memória; o DDL acontece na operação que os recebe.
INERT_SQLALCHEMY = SAFE_COLUMN_TYPES | frozenset(
    {
        "Column", "ForeignKey", "ForeignKeyConstraint", "PrimaryKeyConstraint",
        "UniqueConstraint", "CheckConstraint", "Identity", "Computed", "Enum", "text",
        "literal_column", "true", "false", "null",
    }
)  # fmt: skip
# Opções de `create_table` que não mexem em tabela existente (`postgresql_inherits` trava a mãe).
SAFE_CREATE_TABLE_OPTIONS = frozenset({"schema", "comment", "if_not_exists"})
# Com CONCURRENTLY, nenhuma destas opções trava escrita.
SAFE_CREATE_INDEX_OPTIONS = frozenset(
    {"postgresql_concurrently", "unique", "schema", "if_not_exists", "postgresql_where"}
    | {"postgresql_using", "postgresql_include", "postgresql_ops"}
)
CREATE_INDEX_POSITIONAL = 3
# `sa.Column("nome", tipo, nullable=True, comment="...")`: nome e tipo, nada mais.
NEW_COLUMN_POSITIONAL = 2
ADD_COLUMN_POSITIONAL = 2
# Posição e keyword da tabela-alvo de cada operação do Alembic.
TARGET_TABLE = {
    "create_index": (1, "table_name"),
    "create_unique_constraint": (1, "table_name"),
    "create_primary_key": (1, "table_name"),
    "create_exclude_constraint": (1, "table_name"),
    "create_foreign_key": (1, "source_table"),
    "create_check_constraint": (1, "table_name"),
    "alter_column": (0, "table_name"),
    "add_column": (0, "table_name"),
}

# Operações que trocam a tabela por trás de um nome; anulam a dispensa por tabela nova.
TABLE_SWAPS = frozenset({"drop_table", "rename_table"})
# Só para os checks de CONCURRENTLY, que leem o SQL montado de qualquer `execute`.
EXECUTE_OPERATIONS = frozenset({"execute", "exec_driver_sql"})
# Combinações de textos que uma concatenação pode gerar. Acima disso o resto não é lido, e o texto
# TOO_MANY_RENDERINGS entra no lugar. Ele contém o PLACEHOLDER, então um nome de índice truncado
# também conta como desconhecido.
MAX_RENDERINGS = 16
TOO_MANY_RENDERINGS = f"{PLACEHOLDER}_too_many_renderings"
# Profundidade de nomes resolvidos (um nome que aponta para outro nome).
MAX_NAME_DEPTH = 3
# Keywords que levam o SQL em `op.execute(sqltext=...)` e `exec_driver_sql(statement=...)`.
SQL_KEYWORDS = frozenset({"sqltext", "statement"})
FORMAT_FIELD = re.compile(r"\{[^{}]*\}")
PERCENT_FIELD = re.compile(r"%(\([^)]*\))?[sdr]")
# Chamadas que embrulham o SQL sem mudar o texto, e métodos de texto que só tiram espaço.
SQL_WRAPPERS = frozenset({"text", "DDL", "dedent"})
WHITESPACE_METHODS = frozenset({"strip", "lstrip", "rstrip"})
SQL_OBJECT_METHODS = frozenset({"bindparams", "columns", "execution_options"})

type Trusted = Mapping[str, str]
type Assignments = dict[str, list[ast.expr]]
FUNCTION_NODES = (ast.FunctionDef, ast.AsyncFunctionDef)
type Condition = Callable[[ast.Call, Trusted], bool]


def _create_table_is_safe(call: ast.Call, trusted: Trusted) -> bool:
    """Nome literal, só colunas e constraints do SQLAlchemy, opções literais conhecidas."""
    return (
        bool(call.args)
        and _table_name(call.args[0], trusted) is not None
        and all(_is_inert_call(argument, trusted) for argument in call.args[1:])
        and all(
            keyword.arg in SAFE_CREATE_TABLE_OPTIONS and isinstance(keyword.value, ast.Constant)
            for keyword in call.keywords
        )
    )


def _create_index_is_safe(call: ast.Call, _trusted: Trusted) -> bool:
    return (
        _keyword_constant(call, "postgresql_concurrently") is True
        and len(call.args) <= CREATE_INDEX_POSITIONAL
        and not _unpacks(call)
        and all(keyword.arg in SAFE_CREATE_INDEX_OPTIONS for keyword in call.keywords)
    )


def _add_column_is_safe(call: ast.Call, trusted: Trusted) -> bool:
    """`op.add_column("tabela", sa.Column("nome", tipo))`, posicional e sem opção."""
    return (
        len(call.args) == ADD_COLUMN_POSITIONAL
        and not call.keywords
        and _table_name(call.args[0], trusted) is not None
        and _new_column_is_safe(call.args[1], trusted)
    )


def _execute_is_safe(call: ast.Call, trusted: Trusted) -> bool:
    """SQL literal, direto ou em `sa.text("...")`, que a allowlist de SQL aceita."""
    if len(call.args) != 1 or call.keywords:
        return False
    sql = _literal_sql(call.args[0], trusted)
    return sql is not None and PLACEHOLDER not in sql and not analyze_sql(sql).blocking


def _names_object(call: ast.Call, _trusted: Trusted) -> bool:
    """`op.f(nome)`: só marca o nome como já convertido; as chamadas dentro de `nome` são lidas."""
    return len(call.args) == 1 and not call.keywords and not _unpacks(call)


def _has_no_arguments(call: ast.Call, _trusted: Trusted) -> bool:
    return not call.args and not call.keywords


# A allowlist: operação do Alembic → a forma exata em que ela não trava tabela em uso. Fora daqui
# (drop_column, alter_column, constraint NOT VALID, drop_*, bulk_insert, get_bind...) pede a marca.
SAFE_OPERATIONS: dict[str, Condition] = {
    "create_table": _create_table_is_safe,
    "create_index": _create_index_is_safe,
    "add_column": _add_column_is_safe,
    "execute": _execute_is_safe,
    # Não falam com o banco.
    "f": _names_object,
    "get_context": _has_no_arguments,
}


def checked_revisions(names: list[str]) -> list[str]:
    """Os arquivos de migration que o check cobre: todos os que seguem o nome `NNNN_*.py`."""
    return sorted(name for name in names if REVISION_FILE.match(name))


def unmarked_blocking_lines(source: str) -> list[int]:
    """Linhas que pedem `# lock-ok:` e não a têm, no módulo inteiro salvo o `downgrade()`."""
    tree = ast.parse(source)
    found = comments(source)
    roots = _checked_roots(tree)
    bindings = _binding_counts(tree)
    trusted = _trusted_names(tree, bindings)
    functions = _module_functions(tree, bindings, trusted)
    on_new_table = _calls_on_new_tables(roots, trusted)
    lines = {
        line
        for root in roots
        for node in ast.walk(root)
        for line in _blocking_lines(node, trusted, functions, on_new_table)
    }
    return sorted(line for line in lines if not is_marked(found, line, LOCK_OK_MARK))


def concurrent_in_transaction_lines(source: str) -> list[int]:
    """Linhas que pedem CONCURRENTLY fora de um `autocommit_block()` escrito na mesma função.

    O PostgreSQL recusa CONCURRENTLY na transação da migration, ou seja, fora de um
    `autocommit_block()`, e também num `execute` com mais de uma instrução, que roda numa
    transação implícita. Vale para o módulo inteiro, salvo o que só o `downgrade()` alcança: a
    função auxiliar chamada de dentro do bloco também é recusada, porque a limpeza só é conferida
    dentro do bloco em que a criação está escrita. Nenhuma marca libera.
    """
    tree = ast.parse(source)
    roots = _checked_roots(tree)
    trusted = _trusted_names(tree, _binding_counts(tree))
    assignments = _assignments(tree, _upgrade_scope(tree))
    inside_autocommit = {
        id(call)
        for root in roots
        for block in _autocommit_blocks(root, trusted)
        for call in _calls_in_order(block)
    }
    return sorted(
        node.lineno
        for root in roots
        for node in ast.walk(root)
        if isinstance(node, ast.Call)
        and _is_concurrent(node, assignments)
        and (id(node) not in inside_autocommit or _runs_several_statements(node, assignments))
    )


def concurrent_index_without_cleanup_lines(source: str) -> list[int]:
    """Índices CONCURRENTLY sem `DROP INDEX CONCURRENTLY IF EXISTS` do mesmo nome antes.

    Um CREATE INDEX CONCURRENTLY que estoura o `lock_timeout` deixa o índice INVALID para trás.
    Na nova tentativa, o CREATE falharia com "already exists", ou, com IF NOT EXISTS, aceitaria
    o índice inválido em silêncio; apagar antes torna a repetição segura. Nome que o check não
    resolve nunca conta como apagado: na dúvida, a criação é recusada.
    """
    tree = ast.parse(source)
    trusted = _trusted_names(tree, _binding_counts(tree))
    assignments = _assignments(tree, _upgrade_scope(tree))
    unsafe: list[int] = []
    blocks = (block for root in _checked_roots(tree) for block in _autocommit_blocks(root, trusted))
    for block in blocks:
        # Um DROP em condição, laço ou auxiliar pode não rodar antes da criação.
        direct_calls = {
            statement.value
            for statement in block.body
            if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call)
        }
        dropped: set[IndexName] = set()
        for call in _calls_in_order(block):
            if call in direct_calls:
                dropped.update(
                    name
                    for name in _dropped_concurrent_indexes(call, assignments)
                    if name is not None
                )
            created = _created_concurrent_indexes(call, assignments)
            if any(name is None or name not in dropped for name in created):
                unsafe.append(call.lineno)
            dropped.difference_update(created)
    return sorted(unsafe)


def schema_change_beside_autocommit_lines(source: str) -> list[int]:
    """Mudanças de schema numa revisão com `autocommit_block()`, dentro ou fora dele.

    O bloco faz commit do que veio antes dele, e dentro dele cada instrução é comitada na hora,
    mas o Alembic só grava o carimbo da revisão no fim. Se o CREATE INDEX CONCURRENTLY estoura o
    `lock_timeout`, o retry do ADR 057 roda a revisão inteira de novo, e um ADD COLUMN já
    aplicado falha com DuplicateColumn, sem retry. Passam só `op.get_context()`, `execute` de
    SQL literal em que toda instrução é UPDATE ou DELETE (a idempotência delas fica com a
    revisão do PR) e, dentro do bloco, o `DROP INDEX CONCURRENTLY IF EXISTS` e o CREATE INDEX
    CONCURRENTLY que os checks de CONCURRENTLY reconhecem, uma instrução por chamada. A mudança
    de schema vai para a revisão anterior (Split Phase). Nenhuma marca libera.
    """
    tree = ast.parse(source)
    roots = _checked_roots(tree)
    trusted = _trusted_names(tree, _binding_counts(tree))
    blocks = [block for root in roots for block in _autocommit_blocks(root, trusted)]
    if not blocks:
        return []
    assignments = _assignments(tree, _upgrade_scope(tree))
    inside_autocommit = {id(call) for block in blocks for call in _calls_in_order(block)}
    return sorted(
        node.lineno
        for root in roots
        for node in ast.walk(root)
        if isinstance(node, ast.Call)
        and _changes_schema(node, trusted, assignments)
        and not (id(node) in inside_autocommit and _is_concurrent_index_step(node, assignments))
    )


def _is_concurrent_index_step(call: ast.Call, assignments: Assignments) -> bool:
    """`DROP INDEX CONCURRENTLY IF EXISTS` ou CREATE INDEX CONCURRENTLY, repetíveis no retry."""
    name = _operation(call)
    if name in {"create_index", "drop_index"}:
        return bool(
            _created_concurrent_indexes(call, assignments)
            or _dropped_concurrent_indexes(call, assignments)
        )
    if name in EXECUTE_OPERATIONS:
        texts = _sql_texts(call, assignments)
        return bool(texts) and all(_is_single_concurrent_index_statement(text) for text in texts)
    return False


def _is_single_concurrent_index_statement(sql: str) -> bool:
    facts = analyze_sql(sql)
    return facts.statements == 1 and bool(
        facts.created_concurrent_indexes or facts.dropped_concurrent_indexes
    )


def _changes_schema(call: ast.Call, trusted: Trusted, assignments: Assignments) -> bool:
    """Operação do Alembic que pode mexer no schema; na dúvida, mexe."""
    name = _op_operation(call, trusted)
    if name is None or name == "get_context":
        return False
    if name in EXECUTE_OPERATIONS:
        return not all(only_changes_rows(text) for text in _sql_texts(call, assignments))
    return True


def _checked_roots(tree: ast.Module) -> list[ast.stmt]:
    """O módulo sem as funções que só o `downgrade()` alcança."""
    functions = {node.name: node for node in tree.body if isinstance(node, FUNCTION_NODES)}
    downgrade = _reachable(functions, {"downgrade"})
    outside = {
        name
        for node in tree.body
        if not (isinstance(node, FUNCTION_NODES) and node.name in downgrade)
        for name in _loaded_names(node)
    }
    excluded = downgrade - _reachable(functions, outside | {"upgrade"})
    return [
        node
        for node in tree.body
        if not (isinstance(node, FUNCTION_NODES) and node.name in excluded)
    ]


def _reachable(functions: Mapping[str, ast.stmt], start: set[str]) -> set[str]:
    """As funções do módulo citadas a partir de `start`, chamadas ou passadas por referência."""
    pending, seen = list(start), set()
    while pending:
        name = pending.pop()
        if name in seen or name not in functions:
            continue
        seen.add(name)
        pending.extend(_loaded_names(functions[name]))
    return seen


def _loaded_names(node: ast.AST) -> set[str]:
    return {
        child.id
        for child in ast.walk(node)
        if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load)
    }


def _binding_counts(tree: ast.Module) -> Counter[str]:
    """Quantas vezes cada nome é ligado no módulo, de qualquer forma e em qualquer escopo."""
    counts: Counter[str] = Counter()
    for node in ast.walk(tree):
        match node:
            case ast.Name(id=name, ctx=ast.Store() | ast.Del()) | ast.arg(arg=name):
                counts[name] += 1
            case ast.FunctionDef(name=name) | ast.AsyncFunctionDef(name=name):
                counts[name] += 1
            case ast.ClassDef(name=name) | ast.TypeVar(name=name) | ast.ParamSpec(name=name):
                counts[name] += 1
            case ast.TypeVarTuple(name=name) | ast.ExceptHandler(name=str(name)):
                counts[name] += 1
            case ast.MatchAs(name=str(name)) | ast.MatchStar(name=str(name)):
                counts[name] += 1
            case ast.MatchMapping(rest=str(name)):
                counts[name] += 1
            case ast.alias(name=name, asname=asname):
                counts[asname or name.split(".")[0]] += 1
            case ast.Global(names=names) | ast.Nonlocal(names=names):
                counts.update(names)
    return counts


def _trusted_names(tree: ast.Module, bindings: Counter[str]) -> dict[str, str]:
    """Nome local → o que ele importa (`op` → `alembic.op`, `sa` → `sqlalchemy`).

    Só vale o import que é a única ligação do nome no módulo, sem atributo atribuído ou apagado
    (`op.create_index = op.execute`). `from x import *` pode religar qualquer nome: anula todos.
    """
    imported: dict[str, str] = {}
    for node in ast.walk(tree):
        match node:
            case ast.ImportFrom(names=names) if any(alias.name == "*" for alias in names):
                return {}
            case ast.ImportFrom(module=str(module), level=0, names=names):
                for alias in names:
                    path = f"{module}.{alias.name}"
                    if path == ALEMBIC_OP or _is_sqlalchemy(module):
                        imported[alias.asname or alias.name] = path
            case ast.Import(names=names):
                for alias in names:
                    if _is_sqlalchemy(alias.name):
                        local = alias.asname or alias.name.split(".")[0]
                        imported[local] = alias.name if alias.asname else SQLALCHEMY
    mutated = {
        _root_name(node)
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute | ast.Subscript)
        and isinstance(node.ctx, ast.Store | ast.Del)
    }
    return {
        name: path for name, path in imported.items() if bindings[name] == 1 and name not in mutated
    }


def _is_sqlalchemy(module: str) -> bool:
    return module == SQLALCHEMY or module.startswith(f"{SQLALCHEMY}.")


def _module_functions(tree: ast.Module, bindings: Counter[str], trusted: Trusted) -> frozenset[str]:
    """Funções do módulo, sem decorator, cujo nome não é ligado de outra forma em lugar nenhum."""
    if not trusted and _has_star_import(tree):
        return frozenset()
    return frozenset(
        node.name
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and not node.decorator_list
        and bindings[node.name] == 1
    )


def _has_star_import(tree: ast.Module) -> bool:
    return any(
        isinstance(node, ast.ImportFrom) and any(alias.name == "*" for alias in node.names)
        for node in ast.walk(tree)
    )


def _blocking_lines(
    node: ast.AST, trusted: Trusted, functions: frozenset[str], on_new_table: set[int]
) -> Iterator[int]:
    """As linhas de `node` que rodam código fora da allowlist."""
    match node:
        case ast.Call() if id(node) not in on_new_table and not _call_is_allowed(
            node, trusted, functions
        ):
            yield node.lineno
        case ast.FunctionDef() | ast.AsyncFunctionDef() | ast.ClassDef():
            # O decorator e a base de classe (`__init_subclass__`, metaclasse) rodam código.
            yield from (decorator.lineno for decorator in node.decorator_list)
            if isinstance(node, ast.ClassDef) and (node.bases or node.keywords):
                yield node.lineno
        case ast.With() | ast.AsyncWith():
            # `__enter__` roda código; só o `autocommit_block()` do Alembic é conhecido.
            yield from (
                item.context_expr.lineno
                for item in node.items
                if not _is_autocommit_block(item.context_expr, trusted)
            )


def _call_is_allowed(call: ast.Call, trusted: Trusted, functions: frozenset[str]) -> bool:
    path = _qualified_name(call.func, trusted)
    if path is not None:
        module, _, name = path.rpartition(".")
        if module == ALEMBIC_OP:
            condition = SAFE_OPERATIONS.get(name)
            return condition is not None and condition(call, trusted)
        return _is_inert_path(path)
    if isinstance(call.func, ast.Name):
        return call.func.id in functions
    return _is_autocommit_block(call, trusted)


def _qualified_name(callee: ast.expr, trusted: Trusted) -> str | None:
    """`sa.dialects.postgresql.JSONB` → `sqlalchemy.dialects.postgresql.JSONB`; None sem import."""
    parts: list[str] = []
    while isinstance(callee, ast.Attribute):
        parts.append(callee.attr)
        callee = callee.value
    if not isinstance(callee, ast.Name) or callee.id not in trusted:
        return None
    return ".".join([trusted[callee.id], *reversed(parts)])


def _is_inert_path(path: str) -> bool:
    module, _, name = path.rpartition(".")
    if path.startswith(SQLALCHEMY_FUNC) and len(path) > len(SQLALCHEMY_FUNC):
        return True
    return module in SQLALCHEMY_MODULES and name in INERT_SQLALCHEMY


def _is_inert_call(node: ast.expr, trusted: Trusted) -> bool:
    if not isinstance(node, ast.Call):
        return False
    path = _qualified_name(node.func, trusted)
    return path is not None and _is_inert_path(path)


def _sqlalchemy_name(callee: ast.expr, trusted: Trusted) -> str | None:
    """O nome do construtor do SQLAlchemy chamado (`Column`, `Text`); None se não for um."""
    path = _qualified_name(callee, trusted)
    if path is None:
        return None
    module, _, name = path.rpartition(".")
    return name if module in SQLALCHEMY_MODULES else None


def _is_autocommit_block(node: ast.expr, trusted: Trusted) -> bool:
    """`op.get_context().autocommit_block()`, exatamente."""
    match node:
        case ast.Call(
            func=ast.Attribute(attr="autocommit_block", value=ast.Call() as context),
            args=[],
            keywords=[],
        ):
            return _qualified_name(context.func, trusted) == f"{ALEMBIC_OP}.get_context" and (
                _has_no_arguments(context, trusted)
            )
    return False


def _new_column_is_safe(node: ast.expr, trusted: Trusted) -> bool:
    """`sa.Column("nome", tipo)`, só com `nullable=True` e `comment` literais."""
    if not isinstance(node, ast.Call) or _sqlalchemy_name(node.func, trusted) != "Column":
        return False
    if len(node.args) != NEW_COLUMN_POSITIONAL or not _is_text(node.args[0]):
        return False
    for keyword in node.keywords:
        value = _constant(keyword.value) if isinstance(keyword.value, ast.Constant) else None
        if not (
            (keyword.arg == "nullable" and value is True)
            or (keyword.arg == "comment" and isinstance(value, str))
        ):
            return False
    return _type_is_safe(node.args[1], trusted)


def _type_is_safe(node: ast.expr, trusted: Trusted) -> bool:
    """Tipo de `SAFE_COLUMN_TYPES`, com argumentos e opções literais (o do ARRAY é outro tipo)."""
    callee, arguments, keywords = (
        (node.func, node.args, node.keywords) if isinstance(node, ast.Call) else (node, [], [])
    )
    name = _sqlalchemy_name(callee, trusted)
    if name not in SAFE_COLUMN_TYPES:
        return False
    if not all(
        keyword.arg in SAFE_TYPE_OPTIONS and isinstance(keyword.value, ast.Constant)
        for keyword in keywords
    ):
        return False
    if name == ARRAY_TYPE:
        if not arguments or not _type_is_safe(arguments[0], trusted):
            return False
        arguments = arguments[1:]
    return all(isinstance(argument, ast.Constant) for argument in arguments)


def _literal_sql(node: ast.expr, trusted: Trusted) -> str | None:
    if _is_text(node):
        return str(_constant(node))
    if (
        isinstance(node, ast.Call)
        and _sqlalchemy_name(node.func, trusted) == "text"
        and len(node.args) == 1
        and not node.keywords
        and _is_text(node.args[0])
    ):
        return str(_constant(node.args[0]))
    return None


def _calls_on_new_tables(roots: list[ast.stmt], trusted: Trusted) -> set[int]:
    """Operações do Alembic sobre uma tabela que esta mesma função acabou de criar.

    Só conta o `create_table` de nome literal escrito direto no corpo da função (fora de `if`,
    laço ou `try`), antes da operação: se a função chegou à operação, a tabela nasceu nela, vazia,
    porque o CREATE TABLE de um nome que já existe falha. `drop_table`, `rename_table` ou `execute`
    fora da allowlist entre os dois podem pôr outra tabela no mesmo nome: depois deles, nada é
    dispensado. As colunas não importam aqui: o `create_table` passa pela allowlist como qualquer
    chamada.
    """
    on_new: set[int] = set()
    functions = (
        node for root in roots for node in ast.walk(root) if isinstance(node, FUNCTION_NODES)
    )
    for function in functions:
        created: dict[str, int] = {}
        for statement in function.body:
            match statement:
                case ast.Expr(value=ast.Call() as call) | ast.Assign(value=ast.Call() as call) if (
                    _op_operation(call, trusted) == "create_table"
                    and call.args
                    and not _unpacks(call)
                    and not _has_keyword(call, "if_not_exists", "schema")
                ):
                    name = _table_name(call.args[0], trusted)
                    if name is not None:
                        created.setdefault(name, call.lineno)
        calls = [node for node in ast.walk(function) if isinstance(node, ast.Call)]
        swaps = [call.lineno for call in calls if _may_swap_tables(call, trusted)]
        on_new.update(
            id(node)
            for node in calls
            if (table := _target_table(node, trusted)) in created
            and created[table] < node.lineno
            and not any(created[table] <= swap <= node.lineno for swap in swaps)
        )
    return on_new


def _may_swap_tables(call: ast.Call, trusted: Trusted) -> bool:
    """Chamada que pode apagar ou renomear uma tabela, pondo outra no lugar do nome."""
    operation = _op_operation(call, trusted)
    if operation in TABLE_SWAPS:
        return True
    return operation in EXECUTE_OPERATIONS and not _execute_is_safe(call, trusted)


def _op_operation(call: ast.Call, trusted: Trusted) -> str | None:
    """O nome da operação em `op.nome(...)`, com `op` importado do Alembic; None para o resto."""
    path = _qualified_name(call.func, trusted)
    if path is None:
        return None
    module, _, name = path.rpartition(".")
    return name if module == ALEMBIC_OP else None


def _target_table(call: ast.Call, trusted: Trusted) -> str | None:
    position, keyword = TARGET_TABLE.get(_op_operation(call, trusted) or "", (None, None))
    if position is None or _unpacks(call) or _has_keyword(call, "schema"):
        return None
    if len(call.args) > position:
        return _table_name(call.args[position], trusted)
    node = next((kw.value for kw in call.keywords if kw.arg == keyword), None)
    return None if node is None else _table_name(node, trusted)


def _table_name(node: ast.expr, trusted: Trusted) -> str | None:
    """O nome literal da tabela, também quando passa por `op.f("nome")`."""
    if isinstance(node, ast.Call) and _op_operation(node, trusted) == "f":
        if not _names_object(node, trusted):
            return None
        node = node.args[0]
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _unpacks(call: ast.Call) -> bool:
    """`*args` ou `**kwargs`: a posição e as keywords de verdade ficam invisíveis."""
    return any(isinstance(argument, ast.Starred) for argument in call.args) or any(
        keyword.arg is None for keyword in call.keywords
    )


def _root_name(node: ast.expr) -> str | None:
    while isinstance(node, ast.Attribute | ast.Subscript):
        node = node.value
    return node.id if isinstance(node, ast.Name) else None


def _upgrade_scope(tree: ast.Module) -> list[ast.FunctionDef]:
    """O `upgrade()` e as funções do módulo que ele chama, direta ou indiretamente."""
    functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
    pending, seen = ["upgrade"], []
    while pending:
        name = pending.pop()
        if name in seen or name not in functions or name == "downgrade":
            continue
        seen.append(name)
        pending.extend(
            node.func.id
            for node in ast.walk(functions[name])
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        )
    return [functions[name] for name in seen]


def _assignments(tree: ast.Module, scope: list[ast.FunctionDef]) -> Assignments:
    """Valores atribuídos a cada nome no nível do módulo e nas funções do escopo do upgrade.

    O `downgrade()` fica de fora: um nome igual lá não contamina o `upgrade()`. Na ordem do
    código, `sql += "..."` estende cada valor atual do nome; os valores de antes do acréscimo
    ficam depois deles como alternativas, porque o SQL pode ter rodado antes.
    """
    statements: list[ast.stmt] = [
        node for node in tree.body if not isinstance(node, ast.FunctionDef)
    ]
    statements += [node for function in scope for node in _in_source_order(function)]
    current: Assignments = {}
    earlier: Assignments = {}
    for node in statements:
        if (
            isinstance(node, ast.AugAssign)
            and isinstance(node.op, ast.Add)
            and isinstance(node.target, ast.Name)
        ):
            name = node.target.id
            before = current.get(name) or [ast.Constant(value=PLACEHOLDER)]
            earlier.setdefault(name, []).extend(current.get(name, []))
            current[name] = [
                ast.BinOp(left=value, op=ast.Add(), right=node.value) for value in before
            ]
        elif isinstance(node, ast.Assign | ast.AnnAssign) and node.value is not None:
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    current.setdefault(target.id, []).append(node.value)
    return {
        name: current.get(name, []) + earlier.get(name, [])
        for name in current.keys() | earlier.keys()
    }


def _in_source_order(function: ast.FunctionDef) -> list[ast.stmt]:
    statements = [node for node in ast.walk(function) if isinstance(node, ast.stmt)]
    return sorted(statements, key=lambda node: (node.lineno, node.col_offset))


def _is_text(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, str)


def _constant(node: ast.expr) -> object:
    return node.value if isinstance(node, ast.Constant) else None


def _sql_texts(call: ast.Call, assignments: Assignments) -> list[str]:
    """Os textos de SQL que o `execute` pode receber, com variáveis e f-strings montadas.

    Sem texto à vista (nenhum argumento, ou só `**`), o SQL vira o PLACEHOLDER.
    """
    arguments = [*call.args, *(kw.value for kw in call.keywords if kw.arg in SQL_KEYWORDS)]
    if not arguments:
        return [PLACEHOLDER]
    return [text for argument in arguments for text in _render(argument, assignments, 0)]


def _render(node: ast.expr, assignments: Assignments, depth: int) -> list[str]:
    """Os textos que `node` pode produzir; trecho desconhecido vira o PLACEHOLDER."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.JoinedStr):
        texts = [""]
        for part in node.values:
            options = _render_fstring_part(part, assignments, depth)
            texts = _capped([a + b for a in texts for b in options])
        return texts
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _render(node.left, assignments, depth)
        right = _render(node.right, assignments, depth)
        return _capped([a + b for a in left for b in right])
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
        # `"... %s ..." % valor`: o molde com o PLACEHOLDER no lugar de cada campo.
        return [
            PERCENT_FIELD.sub(PLACEHOLDER, text) for text in _render(node.left, assignments, depth)
        ]
    if isinstance(node, ast.Name) and node.id in assignments and depth < MAX_NAME_DEPTH:
        resolved: list[str] = []
        for value in assignments[node.id]:
            if len(resolved) > MAX_RENDERINGS:
                break
            resolved += _render(value, assignments, depth + 1)
        return _capped(resolved) or [PLACEHOLDER]
    if isinstance(node, ast.Call):
        return _render_call(node, assignments, depth)
    return [PLACEHOLDER]


def _capped(texts: list[str]) -> list[str]:
    """No máximo MAX_RENDERINGS textos; o que passa do teto vira o TOO_MANY_RENDERINGS."""
    if len(texts) <= MAX_RENDERINGS:
        return texts
    return [*texts[: MAX_RENDERINGS - 1], TOO_MANY_RENDERINGS]


def _render_call(call: ast.Call, assignments: Assignments, depth: int) -> list[str]:
    method = call.func if isinstance(call.func, ast.Attribute) else None
    if method is not None and method.attr == "format":
        # `"... {} ...".format(valor)`: o molde com o PLACEHOLDER no lugar de cada campo.
        return [
            FORMAT_FIELD.sub(PLACEHOLDER, text)
            for text in _render(method.value, assignments, depth)
        ]
    if method is not None and method.attr in WHITESPACE_METHODS | SQL_OBJECT_METHODS:
        # `sql.strip()` e `sa.text(sql).bindparams(...)`: o SQL está no objeto de origem.
        return _render(method.value, assignments, depth)
    if _operation(call) in SQL_WRAPPERS and call.args:
        # `sa.text(sql)`, `text(sql)`, `textwrap.dedent(sql)`: o SQL é o primeiro argumento.
        return _render(call.args[0], assignments, depth)
    return [PLACEHOLDER]


def _render_fstring_part(part: ast.expr, assignments: Assignments, depth: int) -> list[str]:
    if isinstance(part, ast.Constant) and isinstance(part.value, str):
        return [part.value]
    if isinstance(part, ast.FormattedValue):
        return _render(part.value, assignments, depth)
    return [PLACEHOLDER]


def _autocommit_blocks(root: ast.AST, trusted: Trusted) -> list[ast.With]:
    return [
        node
        for node in ast.walk(root)
        if isinstance(node, ast.With)
        and any(_is_autocommit_block(item.context_expr, trusted) for item in node.items)
    ]


def _calls_in_order(block: ast.With) -> list[ast.Call]:
    calls = [
        child
        for statement in block.body
        for child in ast.walk(statement)
        if isinstance(child, ast.Call)
    ]
    return sorted(calls, key=lambda call: (call.lineno, call.col_offset))


def _created_concurrent_indexes(call: ast.Call, assignments: Assignments) -> list[IndexName | None]:
    """Nomes dos índices criados com CONCURRENTLY; None para nome que o check não resolve."""
    name = _operation(call)
    if name == "create_index" and _keyword_is_true(call, "postgresql_concurrently"):
        return [_index_name(call)]
    if name in EXECUTE_OPERATIONS:
        return [
            index
            for text in _sql_texts(call, assignments)
            for index in analyze_sql(text).created_concurrent_indexes
        ]
    return []


def _dropped_concurrent_indexes(call: ast.Call, assignments: Assignments) -> list[IndexName | None]:
    """Nomes dos índices apagados com `DROP INDEX CONCURRENTLY IF EXISTS`; None se não resolve."""
    name = _operation(call)
    if (
        name == "drop_index"
        and _keyword_is_true(call, "postgresql_concurrently")
        and _keyword_is_true(call, "if_exists")
    ):
        return [_index_name(call)]
    if name in EXECUTE_OPERATIONS:
        alternatives = [
            set(analyze_sql(text).dropped_concurrent_indexes)
            for text in _sql_texts(call, assignments)
        ]
        return list(set.intersection(*alternatives)) if alternatives else []
    return []


def _index_name(call: ast.Call) -> IndexName | None:
    """O nome dado ao Alembic, exato: ele põe aspas em nome com maiúscula, e a caixa fica."""
    node = call.args[0] if call.args else None
    if node is None:
        node = next((kw.value for kw in call.keywords if kw.arg == "index_name"), None)
    if isinstance(node, ast.Call) and _operation(node) == "f" and node.args:
        node = node.args[0]
    name = _constant(node) if node is not None else None
    if not isinstance(name, str):
        return None
    schema = _keyword_constant(call, "schema")
    return (schema, name) if isinstance(schema, str) else (name,)


def _is_concurrent(call: ast.Call, assignments: Assignments) -> bool:
    name = _operation(call)
    if name in {"create_index", "drop_index"}:
        return _keyword_is_true(call, "postgresql_concurrently")
    if name in EXECUTE_OPERATIONS:
        return any(analyze_sql(text).concurrent for text in _sql_texts(call, assignments))
    return False


def _runs_several_statements(call: ast.Call, assignments: Assignments) -> bool:
    """O `execute` manda mais de uma instrução numa mensagem só."""
    return _operation(call) in EXECUTE_OPERATIONS and any(
        analyze_sql(text).statements > 1 for text in _sql_texts(call, assignments)
    )


def _operation(call: ast.Call) -> str | None:
    """O nome chamado, qualquer que seja o receptor: os checks de CONCURRENTLY leem pelo nome."""
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return call.func.id if isinstance(call.func, ast.Name) else None


def _has_keyword(call: ast.Call, *names: str) -> bool:
    """Alguma das keywords aparece, com qualquer valor que não seja um False literal."""
    return any(
        keyword.arg in names and _constant(keyword.value) is not False for keyword in call.keywords
    )


def _keyword_is_true(call: ast.Call, name: str) -> bool:
    return _keyword_constant(call, name) is True


def _keyword_constant(call: ast.Call, name: str) -> object:
    for keyword in call.keywords:
        if keyword.arg == name and isinstance(keyword.value, ast.Constant):
            return keyword.value.value
    return None
