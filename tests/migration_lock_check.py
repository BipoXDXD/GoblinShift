"""Leitura estática das migrations atrás de DDL que trava a API (ADR 057).

DDL que reescreve ou varre a tabela sob ACCESS EXCLUSIVE ou SHARE (índice sem CONCURRENTLY,
constraint validada na hora, SET NOT NULL, troca de tipo, coluna com default volátil, única,
indexada, com FK ou CHECK) para as escritas, e às vezes as leituras, enquanto roda. A forma
segura existe para cada caso. Quem precisa da forma bloqueante marca `# lock-ok: <motivo>` na
linha da operação ou na linha só de comentário logo acima. Operação sobre uma tabela criada no
mesmo `upgrade()` não trava ninguém e dispensa a marca.

O check lê o `upgrade()` e as funções do módulo que ele chama, resolve texto e colunas guardados
em variável e monta f-strings e concatenações com um marcador no lugar do valor. O SQL montado
vai para `tests/migration_sql.py`, que o lê com o parser do PostgreSQL. Continua estático: SQL
vindo de fora do módulo ou montado em laço escapa, e a revisão do PR vale.
"""

import ast
import re
from collections.abc import Iterator

from tests.migration_source import is_marked
from tests.migration_sql import PLACEHOLDER, VOLATILE_FUNCTION, IndexName, analyze_sql

FIRST_CHECKED_REVISION = 19
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
# Keywords que levam o SQL em `op.execute(sqltext=...)` e `exec_driver_sql(statement=...)`.
SQL_KEYWORDS = frozenset({"sqltext", "statement"})
FORMAT_FIELD = re.compile(r"\{[^{}]*\}")
PERCENT_FIELD = re.compile(r"%(\([^)]*\))?[sdr]")
# Constraints que constroem índice sob lock: a forma segura é criar o índice com CONCURRENTLY
# antes e anexá-lo com `USING INDEX`, o que o Alembic não expressa nestas chamadas.
INDEX_BUILDING_CONSTRAINTS = frozenset(
    {"create_unique_constraint", "create_primary_key", "create_exclude_constraint"}
)
VALIDATING_CONSTRAINTS = frozenset({"create_foreign_key", "create_check_constraint"})
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
# Opções de `sa.Column` que viram índice ou constraint construídos sob lock.
INDEXED_COLUMN_FLAGS = ("unique", "index", "primary_key")
# Partes de `sa.Column` que varrem a tabela ao entrar: FK e CHECK são validadas na hora.
VALIDATED_COLUMN_PARTS = frozenset({"ForeignKey", "CheckConstraint"})
VOLATILE_DEFAULT = re.compile(rf"\b({VOLATILE_FUNCTION.pattern})\b", re.IGNORECASE)

type Assignments = dict[str, list[ast.expr]]


def checked_revisions(names: list[str]) -> list[str]:
    """Os arquivos de migration que o check cobre: da FIRST_CHECKED_REVISION em diante."""
    return sorted(
        name
        for name in names
        if (match := REVISION_FILE.match(name)) and int(match.group(1)) >= FIRST_CHECKED_REVISION
    )


def unmarked_blocking_lines(source: str) -> list[int]:
    """Linhas do `upgrade()`, e das funções que ele chama, com DDL bloqueante sem `# lock-ok:`."""
    tree = ast.parse(source)
    lines = source.splitlines()
    scope = _upgrade_scope(tree)
    assignments = _assignments(tree, scope)
    created = _tables_created(scope)
    return sorted(
        node.lineno
        for function in scope
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and _target_table(node) not in created
        and _is_blocking(node, assignments)
        and not is_marked(lines, node.lineno, LOCK_OK_MARK)
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
    statements: list[ast.AST] = [
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


def _tables_created(scope: list[ast.FunctionDef]) -> frozenset[str]:
    names = (
        _table_name(node.args[0])
        for function in scope
        for node in ast.walk(function)
        if isinstance(node, ast.Call) and _operation(node) == "create_table" and node.args
    )
    return frozenset(name for name in names if name is not None)


def _target_table(call: ast.Call) -> str | None:
    position, keyword = TARGET_TABLE.get(_operation(call) or "", (None, None))
    if position is None:
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


def _is_blocking(call: ast.Call, assignments: Assignments) -> bool:
    name = _operation(call)
    if name == "create_index":
        return not _keyword_is_true(call, "postgresql_concurrently")
    if name in INDEX_BUILDING_CONSTRAINTS:
        return True
    if name in VALIDATING_CONSTRAINTS:
        return not _keyword_is_true(call, "postgresql_not_valid")
    if name == "alter_column":
        return _keyword_is_false(call, "nullable") or _has_keyword(call, "type_")
    if name == "add_column":
        return _adds_rewriting_column(call, assignments)
    if name in EXECUTE_OPERATIONS:
        return any(_is_blocking_sql(text) for text in _sql_texts(call, assignments))
    return False


def _adds_rewriting_column(call: ast.Call, assignments: Assignments) -> bool:
    """`add_column` que reescreve a tabela, a varre para validar ou constrói índice sob lock."""
    for node in _expanded_nodes(call, assignments):
        if not isinstance(node, ast.Call):
            continue
        kind = _operation(node)
        if kind == "Identity" or kind in VALIDATED_COLUMN_PARTS:
            return True
        if kind == "Computed" and _keyword_constant(node, "persisted") is not False:
            return True
        if any(_keyword_is_true(node, flag) for flag in INDEXED_COLUMN_FLAGS):
            return True
        default = next((kw.value for kw in node.keywords if kw.arg == "server_default"), None)
        if default is not None and _has_volatile_default(default, assignments):
            return True
    return False


def _has_volatile_default(default: ast.expr, assignments: Assignments) -> bool:
    """O default chama função volátil: em texto, em `sa.func.<nome>()` ou por variável."""
    for node in _expanded_nodes(default, assignments):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            text = node.value
        elif isinstance(node, ast.Attribute):
            text = node.attr
        elif isinstance(node, ast.Name):
            text = node.id
        else:
            continue
        if VOLATILE_DEFAULT.search(text):
            return True
    return False


def _is_blocking_sql(sql: str) -> bool:
    return TOO_MANY_RENDERINGS in sql or analyze_sql(sql).blocking


def _sql_texts(call: ast.Call, assignments: Assignments) -> list[str]:
    """Os textos de SQL que o `execute` pode receber, com variáveis e f-strings montadas."""
    arguments = [*call.args, *(kw.value for kw in call.keywords if kw.arg in SQL_KEYWORDS)]
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
    if method is not None and isinstance(method.value, ast.Call):
        # `sa.text(sql).bindparams(...)` e afins: o SQL está no objeto de origem.
        return _render(method.value, assignments, depth)
    if call.args:
        # `sa.text(sql)`, `text(sql)` e afins: o SQL é o primeiro argumento.
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


def _has_keyword(call: ast.Call, name: str) -> bool:
    return any(keyword.arg == name for keyword in call.keywords)


def _keyword_is_true(call: ast.Call, name: str) -> bool:
    return _keyword_constant(call, name) is True


def _keyword_is_false(call: ast.Call, name: str) -> bool:
    return _keyword_constant(call, name) is False


def _keyword_constant(call: ast.Call, name: str) -> object:
    for keyword in call.keywords:
        if keyword.arg == name and isinstance(keyword.value, ast.Constant):
            return keyword.value.value
    return None
