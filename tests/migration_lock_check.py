"""Leitura estática das migrations atrás de DDL que trava a API (ADR 057).

DDL que reescreve ou varre a tabela sob ACCESS EXCLUSIVE ou SHARE (índice sem CONCURRENTLY,
constraint validada na hora, SET NOT NULL, troca de tipo, coluna com default volátil, única,
indexada, com FK ou CHECK) para as escritas, e às vezes as leituras, enquanto roda. A forma
segura existe para cada caso.

O check nega por padrão: só passa sem a marca a operação do Alembic que está em `SAFE_OPERATIONS`
na forma segura, e o SQL cujas instruções estão na allowlist de `tests/migration_sql.py`.
Operação nova ou desconhecida (`rename_table`, `batch_alter_table`, opção nova de `alter_column`)
pede a marca, mesmo que não trave nada. Quem precisa dela marca `# lock-ok: <motivo>` na linha
da operação ou na linha só de comentário logo acima. Operação sobre uma tabela que a mesma
função acabou de criar não trava ninguém e dispensa a marca.

O check lê o módulo inteiro, salvo as funções que só o `downgrade()` alcança, resolve texto e
colunas guardados em variável e monta f-strings e concatenações com um marcador no lugar do
valor que não resolve; SQL com esse marcador pede a marca. O SQL montado vai para
`tests/migration_sql.py`, que o lê com o parser do PostgreSQL. Continua estático: SQL vindo de
fora do módulo escapa, e a revisão do PR vale.
"""

import ast
import re
from collections.abc import Callable, Iterator, Mapping

import alembic.op

from tests.migration_source import comments, is_marked
from tests.migration_sql import PLACEHOLDER, IndexName, analyze_sql, default_sql_is_safe

REVISION_FILE = re.compile(r"^(\d{4})_\w+\.py$")
LOCK_OK_MARK = re.compile(r"#\s*lock-ok:\s*\S")
# Combinações de textos que uma concatenação pode gerar. Acima disso o resto não é lido, e o texto
# TOO_MANY_RENDERINGS entra no lugar: na dúvida, o SQL conta como bloqueante e pede a marca. Ele
# contém o PLACEHOLDER, então um nome de índice truncado também conta como desconhecido.
MAX_RENDERINGS = 16
TOO_MANY_RENDERINGS = f"{PLACEHOLDER}_too_many_renderings"
# Profundidade de nomes resolvidos (um nome que aponta para outro nome).
MAX_NAME_DEPTH = 3

EXECUTE_OPERATIONS = frozenset({"execute", "exec_driver_sql"})
# Método que roda SQL em qualquer receptor (`execute`, `executemany`, `_exec`, `exec_driver_sql`).
DATABASE_METHOD = re.compile(r"exec", re.IGNORECASE)
# Opções de `create_table` que não mexem em tabela existente; `postgresql_inherits` trava a mãe.
SAFE_CREATE_TABLE_OPTIONS = frozenset(
    {"schema", "if_not_exists", "comment", "info", "postgresql_partition_by"}
    | {"postgresql_with", "postgresql_using", "postgresql_tablespace"}
)
# Valor de um nome que o check não resolve: parâmetro, variável de laço, desempacotamento,
# classe ou função do módulo. Lido como texto, vira o PLACEHOLDER; como coluna ou tipo, é recusado.
UNRESOLVED = ast.Name(id=PLACEHOLDER)
# Valor de um nome importado: texto desconhecido, mas tipo externo (`from sqlalchemy import Text`).
IMPORTED = ast.Name(id=PLACEHOLDER)
# Keywords que levam o SQL em `op.execute(sqltext=...)` e `exec_driver_sql(statement=...)`.
SQL_KEYWORDS = frozenset({"sqltext", "statement"})
FORMAT_FIELD = re.compile(r"\{[^{}]*\}")
PERCENT_FIELD = re.compile(r"%(\([^)]*\))?[sdr]")
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
# Opções de `sa.Column` que não viram índice, constraint nem reescrita; `default` e `onupdate`
# ficam no Python. Fora daqui (unique, index, primary_key, autoincrement...) pede a marca.
SAFE_COLUMN_OPTIONS = frozenset(
    {"nullable", "server_default", "comment", "doc", "info", "key", "quote", "name", "type_"}
    | {"default", "onupdate"}
)
SAFE_ADD_COLUMN_OPTIONS = frozenset({"schema", "if_not_exists"})
# `sa.true()`, `sa.false()` e `sa.null()` viram constantes no DEFAULT.
CONSTANT_DEFAULT_CALLS = frozenset({"true", "false", "null"})
SQL_FUNCTIONS = frozenset({"func"})
# Tipo de coluna do SQLAlchemy (classe: `sa.Text`, `postgresql.JSONB`), salvo o domínio, que pode
# ter CHECK e varrer a tabela.
DOMAIN_TYPE = "DOMAIN"
# Chamadas que embrulham o SQL sem mudar o texto, e métodos de texto que só tiram espaço. Outro
# método (`replace`, `join`...) não é lido: o resultado vira o PLACEHOLDER e pede a marca.
SQL_WRAPPERS = frozenset({"text", "DDL", "dedent"})
WHITESPACE_METHODS = frozenset({"strip", "lstrip", "rstrip"})
SQL_OBJECT_METHODS = frozenset({"bindparams", "columns", "execution_options"})
# Opções de `alter_column` que só mexem no catálogo; `existing_*` só informam o estado atual.
SAFE_ALTER_COLUMN_OPTIONS = frozenset(
    {
        "nullable",
        "server_default",
        "comment",
        "schema",
        "existing_type",
        "existing_nullable",
        "existing_server_default",
        "existing_comment",
    }
)
ALTER_COLUMN_POSITIONAL = 2
# Nomes do `alembic.op`: chamados soltos (`create_index(...)`, de `import *` ou de um apelido)
# também são operações.
ALEMBIC_OPERATIONS = frozenset(name for name in dir(alembic.op) if not name.startswith("_"))

type Assignments = dict[str, list[ast.expr]]
FUNCTION_NODES = (ast.FunctionDef, ast.AsyncFunctionDef)
type Condition = Callable[[ast.Call, Assignments], bool]


def _always(_call: ast.Call, _assignments: Assignments) -> bool:
    return True


# A allowlist: operação do Alembic → condição em que ela não trava tabela em uso. UNIQUE, PK e
# EXCLUDE ficam de fora: constroem índice sob lock, e o Alembic não expressa o `USING INDEX` de
# um índice criado antes com CONCURRENTLY (esse caminho vai por `op.execute`).
SAFE_OPERATIONS: dict[str, Condition] = {
    "create_table": lambda call, _: _create_table_is_safe(call),
    "create_index": lambda call, _: _keyword_is_true(call, "postgresql_concurrently"),
    "drop_index": lambda call, _: _keyword_is_true(call, "postgresql_concurrently"),
    "add_column": lambda call, assignments: _add_column_is_safe(call, assignments),
    "drop_column": _always,
    "alter_column": lambda call, _: _alter_column_is_safe(call),
    "create_foreign_key": lambda call, _: _keyword_is_true(call, "postgresql_not_valid"),
    "create_check_constraint": lambda call, _: _keyword_is_true(call, "postgresql_not_valid"),
    "drop_constraint": _always,
    # DML: trava linhas, não a tabela.
    "bulk_insert": _always,
    "execute": lambda call, assignments: _sql_is_safe(call, assignments),
    "exec_driver_sql": lambda call, assignments: _sql_is_safe(call, assignments),
    # Não falam com o banco.
    "f": _always,
    "get_bind": _always,
    "get_context": _always,
    "autocommit_block": _always,
    "cursor": _always,
    "inline_literal": _always,
}


def checked_revisions(names: list[str]) -> list[str]:
    """Os arquivos de migration que o check cobre: todos os que seguem o nome `NNNN_*.py`."""
    return sorted(name for name in names if REVISION_FILE.match(name))


def unmarked_blocking_lines(source: str) -> list[int]:
    """Linhas com DDL bloqueante sem `# lock-ok:`, no módulo inteiro salvo o `downgrade()`.

    Ler só o `upgrade()` e o que ele chama pelo nome deixaria escapar a função chamada por
    apelido, a lambda, o método de classe e o `upgrade = outra_funcao`.
    """
    tree = ast.parse(source)
    found = comments(source)
    roots = _checked_roots(tree)
    assignments = _module_assignments(roots)
    on_new_table = _calls_on_new_tables(roots)
    modules, imported = _op_module_names(tree), _imported_operations(tree)
    return sorted(
        node.lineno
        for root in roots
        for node in ast.walk(root)
        if isinstance(node, ast.Call)
        and _reaches_database(node, modules, imported, assignments)
        and id(node) not in on_new_table
        and not _is_safe(node, assignments)
        and not is_marked(found, node.lineno, LOCK_OK_MARK)
    )


def concurrent_in_transaction_lines(source: str) -> list[int]:
    """Linhas do `upgrade()` que pedem CONCURRENTLY dentro de uma transação.

    O PostgreSQL recusa CONCURRENTLY na transação da migration, ou seja, fora de um
    `autocommit_block()`, e também num `execute` com mais de uma instrução, que roda numa
    transação implícita. Nenhuma marca libera.
    """
    tree = ast.parse(source)
    scope = _upgrade_scope(tree)
    if not scope:
        return []
    upgrade, assignments = scope[0], _assignments(tree, scope)
    inside_autocommit = {
        id(call) for block in _autocommit_blocks(upgrade) for call in _calls_in_order(block)
    }
    return sorted(
        node.lineno
        for node in ast.walk(upgrade)
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
    scope = _upgrade_scope(tree)
    if not scope:
        return []
    upgrade, assignments = scope[0], _assignments(tree, scope)
    unsafe: list[int] = []
    for block in _autocommit_blocks(upgrade):
        dropped: set[IndexName] = set()
        for call in _calls_in_order(block):
            dropped.update(
                name for name in _dropped_concurrent_indexes(call, assignments) if name is not None
            )
            created = _created_concurrent_indexes(call, assignments)
            if any(name is None or name not in dropped for name in created):
                unsafe.append(call.lineno)
    return sorted(unsafe)


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


def _module_assignments(roots: list[ast.stmt]) -> Assignments:
    """As atribuições do módulo lido, com o UNRESOLVED para todo nome ligado de outra forma."""
    assignments = _assignments_in_order(
        sorted(
            (node for root in roots for node in ast.walk(root) if isinstance(node, ast.stmt)),
            key=lambda node: (node.lineno, node.col_offset),
        )
    )
    for name in _unresolved_bindings(roots):
        assignments.setdefault(name, []).append(UNRESOLVED)
    for name in _imported_names(roots):
        assignments.setdefault(name, []).append(IMPORTED)
    return assignments


def _imported_names(roots: list[ast.stmt]) -> set[str]:
    return {
        node.asname or node.name.split(".")[0]
        for root in roots
        for node in ast.walk(root)
        if isinstance(node, ast.alias)
    }


def _unresolved_bindings(roots: list[ast.stmt]) -> set[str]:
    """Nomes ligados por laço, parâmetro, desempacotamento, `with`, `except`, `match`, classe ou
    função."""
    simple = {
        id(target)
        for root in roots
        for node in ast.walk(root)
        if isinstance(node, ast.Assign | ast.AnnAssign | ast.AugAssign)
        for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
        if isinstance(target, ast.Name)
    }
    names: set[str] = set()
    for root in roots:
        for node in ast.walk(root):
            match node:
                case ast.Name(ctx=ast.Store()) if id(node) not in simple:
                    names.add(node.id)
                case ast.arg(arg=name) | ast.ExceptHandler(name=str(name)):
                    names.add(name)
                case ast.FunctionDef(name=name) | ast.AsyncFunctionDef(name=name):
                    names.add(name)
                case ast.ClassDef(name=name) | ast.MatchAs(name=str(name)):
                    names.add(name)
                case ast.MatchStar(name=str(name)):
                    names.add(name)
    return names


def _calls_on_new_tables(roots: list[ast.stmt]) -> set[int]:
    """Chamadas sobre uma tabela que esta mesma função acabou de criar.

    Só conta o `create_table` seguro escrito direto no corpo da função (fora de `if`, laço ou
    `try`), antes da operação: se a função chegou à operação, a tabela nasceu nela, vazia.
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
                    _operation(call) == "create_table"
                    and call.args
                    and _create_table_is_safe(call)
                    and not _has_keyword(call, "if_not_exists", "schema")
                ):
                    name = _table_name(call.args[0])
                    if name is not None:
                        created.setdefault(name, call.lineno)
        on_new.update(
            id(node)
            for node in ast.walk(function)
            if isinstance(node, ast.Call)
            and (table := _target_table(node)) in created
            and created[table] < node.lineno
        )
    return on_new


def _create_table_is_safe(call: ast.Call) -> bool:
    """Tabela nova, sem `**` nem opção fora da allowlist (`postgresql_inherits` trava a mãe)."""
    return all(keyword.arg in SAFE_CREATE_TABLE_OPTIONS for keyword in call.keywords)


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
    return _assignments_in_order(statements)


def _assignments_in_order(statements: list[ast.stmt]) -> Assignments:
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


def _target_table(call: ast.Call) -> str | None:
    position, keyword = TARGET_TABLE.get(_operation(call) or "", (None, None))
    if position is None or _has_keyword(call, "schema"):
        return None
    if len(call.args) > position:
        return _table_name(call.args[position])
    node = next((kw.value for kw in call.keywords if kw.arg == keyword), None)
    return None if node is None else _table_name(node)


def _table_name(node: ast.expr) -> str | None:
    """O nome literal da tabela, também quando passa por `op.f("nome")`."""
    if isinstance(node, ast.Call) and _operation(node) == "f" and node.args:
        node = node.args[0]
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _op_module_names(tree: ast.Module) -> frozenset[str]:
    """`op` e os apelidos do módulo: `from alembic import op as ops`, `import alembic.op as o`."""
    aliases = {
        alias.asname
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom | ast.Import)
        for alias in node.names
        if alias.asname
        and (
            (isinstance(node, ast.ImportFrom) and node.module == "alembic" and alias.name == "op")
            or (isinstance(node, ast.Import) and alias.name == "alembic.op")
        )
    }
    return frozenset({"op", *aliases})


def _imported_operations(tree: ast.Module) -> frozenset[str]:
    """Operações importadas com outro nome: `from alembic.op import create_index as ci`."""
    return frozenset(
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "alembic.op"
        for alias in node.names
    )


def _reaches_database(
    call: ast.Call, modules: frozenset[str], imported: frozenset[str], assignments: Assignments
) -> bool:
    """Operação do Alembic, por qualquer receptor, SQL por uma conexão ou método do que vem do op.

    O receptor não decide: `alembic.op.x`, `o = op; o.x` e o `batch` de `batch_alter_table`
    contam pelo nome da operação, e todo método com `exec` no nome conta (`cur.executemany`).
    Qualquer método sobre o que sai do `op` (`op.get_context().impl._exec`, o cursor da conexão)
    também conta. Chamada de algo que não é nome (`getattr(op, "x")(...)`) conta sempre.
    """
    match call.func:
        case ast.Attribute(value=ast.Name(id=module)) if module in modules:
            return True
        case ast.Attribute(attr=attribute, value=receiver):
            return (
                attribute in EXECUTE_OPERATIONS
                or attribute in ALEMBIC_OPERATIONS
                or DATABASE_METHOD.search(attribute) is not None
                or _comes_from_op(receiver, modules, assignments, 0)
            )
        case ast.Name(id=name):
            return name in imported or name in ALEMBIC_OPERATIONS
    return True


def _comes_from_op(
    node: ast.expr, modules: frozenset[str], assignments: Assignments, depth: int
) -> bool:
    """O valor sai do módulo `op`, direto (`op.get_bind().connection`) ou por variável."""
    match node:
        case ast.Attribute(value=inner) | ast.Subscript(value=inner) | ast.Call(func=inner):
            return _comes_from_op(inner, modules, assignments, depth)
        case ast.Name(id=name) if name in modules:
            return True
        case ast.Name(id=name) if name in assignments and depth < MAX_NAME_DEPTH:
            return any(
                _comes_from_op(value, modules, assignments, depth + 1)
                for value in assignments[name]
            )
    return False


def _is_safe(call: ast.Call, assignments: Assignments) -> bool:
    condition = SAFE_OPERATIONS.get(_operation(call) or "")
    return condition is not None and condition(call, assignments)


def _alter_column_is_safe(call: ast.Call) -> bool:
    """Só opções de catálogo; `nullable` só pode ser um True literal (False varre a tabela)."""
    options = {keyword.arg for keyword in call.keywords}
    return (
        len(call.args) <= ALTER_COLUMN_POSITIONAL
        and None not in options
        and options <= SAFE_ALTER_COLUMN_OPTIONS
        and ("nullable" not in options or _keyword_is_true(call, "nullable"))
    )


def _sql_is_safe(call: ast.Call, assignments: Assignments) -> bool:
    return not any(_is_blocking_sql(text) for text in _sql_texts(call, assignments))


def _add_column_is_safe(call: ast.Call, assignments: Assignments) -> bool:
    """Coluna literal (`sa.Column(...)`) só com partes que não reescrevem, varrem nem indexam."""
    options = {keyword.arg for keyword in call.keywords}
    if not options <= SAFE_ADD_COLUMN_OPTIONS | {"column", "table_name"}:
        return False
    column = call.args[1] if len(call.args) > 1 else _keyword_value(call, "column")
    return column is not None and all(
        isinstance(value, ast.Call)
        and _operation(value) == "Column"
        and _column_is_safe(value, assignments)
        for value in _alternatives(column, assignments)
    )


def _column_is_safe(column: ast.Call, assignments: Assignments) -> bool:
    if any(keyword.arg is None for keyword in column.keywords) or any(
        isinstance(argument, ast.Starred) for argument in column.args
    ):
        return False
    options = {keyword.arg: keyword.value for keyword in column.keywords if keyword.arg}
    if not set(options) <= SAFE_COLUMN_OPTIONS:
        return False
    nullable = options.get("nullable")
    if nullable is not None and not (
        isinstance(nullable, ast.Constant) and isinstance(nullable.value, bool)
    ):
        return False
    default = options.get("server_default")
    if default is not None and not _server_default_is_safe(default, assignments):
        return False
    # O primeiro posicional é o nome quando há mais de um; depois vêm o tipo e as partes.
    parts = column.args[1:] if len(column.args) > 1 else column.args
    types = [options["type_"]] if "type_" in options else []
    if parts and not _is_text(parts[0]):
        types.append(parts[0])
        parts = parts[1:]
    elif parts:
        parts = parts[1:]
    return all(_type_is_safe(node, assignments) for node in types) and all(
        _is_virtual_column(part) for part in parts
    )


def _server_default_is_safe(default: ast.expr, assignments: Assignments) -> bool:
    """Constante ou função estável: em texto, em `sa.text(...)`, `sa.true()` ou `sa.func.now()`."""
    return all(_single_default_is_safe(value) for value in _alternatives(default, assignments))


def _single_default_is_safe(default: ast.expr) -> bool:
    if _is_text(default):
        return True
    if not isinstance(default, ast.Call) or default.keywords:
        return False
    name = _operation(default)
    if name in CONSTANT_DEFAULT_CALLS:
        return not default.args
    if name == "text":
        return (
            len(default.args) == 1
            and _is_text(default.args[0])
            and default_sql_is_safe(str(_constant(default.args[0])))
        )
    match default.func:
        case ast.Attribute(value=ast.Attribute(attr=module) | ast.Name(id=module)) if (
            module in SQL_FUNCTIONS
        ):
            return default_sql_is_safe(f"{name}()") and not default.args
    return False


def _type_is_safe(node: ast.expr, assignments: Assignments) -> bool:
    """Classe de tipo do SQLAlchemy (`sa.Text()`, `postgresql.JSONB`), sem domínio nem CHECK.

    Toda chamada dentro do tipo (`sa.ARRAY(sa.Text())`) precisa ser de uma classe que não vem
    do módulo: uma classe local (`UserDefinedType`) pode devolver o nome de um domínio. Nenhuma
    pode ter `**` nem `create_constraint` (o `Boolean` e o `Enum` não nativo viram CHECK).
    """
    for value in _alternatives(node, assignments):
        if not _is_type_class(value.func if isinstance(value, ast.Call) else value, assignments):
            return False
        for child in ast.walk(value):
            if isinstance(child, ast.Attribute | ast.Name) and DOMAIN_TYPE in _node_name(child):
                return False
            if isinstance(child, ast.Call) and not (
                _is_type_class(child.func, assignments)
                and all(keyword.arg is not None for keyword in child.keywords)
                and _keyword_constant(child, "create_constraint") in {None, False}
            ):
                return False
    return True


def _is_type_class(callee: ast.expr, assignments: Assignments) -> bool:
    """Nome de classe (maiúscula) que não é do módulo; nome resolvido conta por cada valor."""
    return _is_class_name(callee) and all(
        value is IMPORTED or _is_class_name(value) for value in _alternatives(callee, assignments)
    )


def _is_class_name(node: ast.expr) -> bool:
    return isinstance(node, ast.Attribute | ast.Name) and _node_name(node)[:1].isupper()


def _node_name(node: ast.Attribute | ast.Name) -> str:
    return node.attr if isinstance(node, ast.Attribute) else node.id


def _is_virtual_column(part: ast.expr) -> bool:
    """`sa.Computed(..., persisted=False)`: coluna gerada VIRTUAL, que não é gravada."""
    return (
        isinstance(part, ast.Call)
        and _operation(part) == "Computed"
        and _keyword_constant(part, "persisted") is False
    )


def _alternatives(node: ast.expr, assignments: Assignments, depth: int = 0) -> list[ast.expr]:
    """Os valores que `node` pode ter: um nome atribuído vira cada valor que recebeu."""
    if isinstance(node, ast.Name) and node.id in assignments and depth < MAX_NAME_DEPTH:
        return [
            alternative
            for value in assignments[node.id]
            for alternative in _alternatives(value, assignments, depth + 1)
        ]
    return [node]


def _is_text(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, str)


def _constant(node: ast.expr) -> object:
    return node.value if isinstance(node, ast.Constant) else None


def _is_blocking_sql(sql: str) -> bool:
    """Trecho que o check não montou (PLACEHOLDER) pode ser qualquer SQL: conta como bloqueante."""
    return PLACEHOLDER in sql or analyze_sql(sql).blocking


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


def _expanded_nodes(root: ast.AST, assignments: Assignments) -> Iterator[ast.AST]:
    """Os nós de `root` e dos valores atribuídos aos nomes que ele usa."""
    pending: list[tuple[ast.AST, int]] = [(root, 0)]
    while pending:
        node, depth = pending.pop()
        for child in ast.walk(node):
            yield child
            if isinstance(child, ast.Name) and depth < MAX_NAME_DEPTH:
                pending.extend((value, depth + 1) for value in assignments.get(child.id, []))


def _autocommit_blocks(upgrade: ast.FunctionDef) -> list[ast.With]:
    return [
        node
        for node in ast.walk(upgrade)
        if isinstance(node, ast.With) and any(_is_autocommit(item) for item in node.items)
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
        return [
            index
            for text in _sql_texts(call, assignments)
            for index in analyze_sql(text).dropped_concurrent_indexes
        ]
    return []


def _index_name(call: ast.Call) -> IndexName | None:
    """O nome dado ao Alembic, exato: ele põe aspas em nome com maiúscula, e a caixa fica."""
    node = call.args[0] if call.args else None
    if node is None:
        node = next((kw.value for kw in call.keywords if kw.arg == "index_name"), None)
    name = None if node is None else _table_name(node)
    if name is None:
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


def _is_autocommit(item: ast.withitem) -> bool:
    expression = item.context_expr
    return isinstance(expression, ast.Call) and _operation(expression) == "autocommit_block"


def _operation(call: ast.Call) -> str | None:
    """O nome chamado: `op.create_index` e `create_index` importado direto dão o mesmo nome."""
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return call.func.id if isinstance(call.func, ast.Name) else None


def _has_keyword(call: ast.Call, *names: str) -> bool:
    """Alguma das keywords aparece, com qualquer valor que não seja um False literal."""
    return any(
        keyword.arg in names and _constant(keyword.value) is not False for keyword in call.keywords
    )


def _keyword_value(call: ast.Call, name: str) -> ast.expr | None:
    return next((keyword.value for keyword in call.keywords if keyword.arg == name), None)


def _keyword_is_true(call: ast.Call, name: str) -> bool:
    return _keyword_constant(call, name) is True


def _keyword_constant(call: ast.Call, name: str) -> object:
    for keyword in call.keywords:
        if keyword.arg == name and isinstance(keyword.value, ast.Constant):
            return keyword.value.value
    return None
