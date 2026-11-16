"""O que um texto de SQL de migration faz quanto a locks, lido pelo parser do PostgreSQL (ADR 057).

O pglast embute o parser do próprio PostgreSQL (libpg_query), então texto entre aspas, dollar
quoting, comentários e nomes com aspas seguem as regras do servidor, sem expressão regular.
O texto é dividido em instruções pelo scanner e cada uma é lida à parte: uma instrução que não
parseia (valor desconhecido no lugar de uma palavra-chave, por exemplo) não esconde as outras.

Nega por padrão, com a allowlist mínima: só passam sem a marca CREATE TABLE de tabela nova,
CREATE INDEX CONCURRENTLY e ALTER TABLE em que todo subcomando é ADD COLUMN anulável de tipo
nativo, sem default nem outra cláusula. Qualquer outra instrução, opção ou cláusula pede a marca,
mesmo que não trave nada (DML, DROP, SET...): quem a conhece decide e escreve o motivo.

Na dúvida o resultado peca pelo lado seguro: instrução que não parseia pede a marca, CONCURRENTLY
no meio dela conta, e índice criado sem nome resolvido nunca conta como apagado.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pglast import ast, parse_sql, split
from pglast.enums import AlterTableType, ConstrType, ObjectType
from pglast.parser import ParseError, scan

# Valor desconhecido na montagem do SQL (variável, campo de f-string): vira um identificador.
PLACEHOLDER = "__unresolved__"
# Nome de índice em partes (schema, nome), como o PostgreSQL o guarda.
type IndexName = tuple[str, ...]

# Tipos nativos de coluna nova. Os de sintaxe própria vêm qualificados por `pg_catalog`; outro
# nome pode ser domínio com CHECK (varre a tabela) ou serial (reescreve), então pede a marca.
BUILTIN_TYPES = frozenset(
    {
        "text", "varchar", "bpchar", "char", "jsonb", "json", "uuid", "bytea", "date", "time",
        "timetz", "timestamp", "timestamptz", "interval", "int2", "int4", "int8", "float4",
        "float8", "numeric", "bool", "cidr", "inet", "macaddr", "tsvector", "xml", "oid",
    }
)  # fmt: skip
CATALOG_SCHEMA = "pg_catalog"
NON_CODE_TOKENS = frozenset({"C_COMMENT", "SQL_COMMENT", "ASCII_59"})
QUALIFIED_NAME_PARTS = 2
# Campos de ColumnDef que ficam vazios numa coluna sem cláusula: `\x00` é o char vazio do pglast.
EMPTY_CHAR = "\x00"
# Campos do ColumnDef que um ADD COLUMN simples preenche. Qualquer outro com valor é cláusula que
# o check não reconhece (COLLATE, COMPRESSION, STORAGE, campo novo do pglast) e exige a marca.
PLAIN_COLUMN_FIELDS = frozenset({"colname", "typeName", "constraints", "is_local", "location"})
UNSET_VALUES = (None, False, "", EMPTY_CHAR)

# A allowlist mínima: tipo de nó → condição em que ele não reescreve nem varre tabela em uso.
SAFE_STATEMENTS: dict[type[Any], Callable[[Any], bool]] = {
    # Tabela nova; PARTITION OF e INHERITS travam a tabela-mãe.
    # INHERITS e PARTITION OF preenchem `inhRelations`: as duas travam a tabela mãe.
    ast.CreateStmt: lambda node: not node.inhRelations,
    ast.IndexStmt: lambda node: bool(node.concurrent),
    ast.AlterTableStmt: lambda node: (
        node.objtype == ObjectType.OBJECT_TABLE
        and bool(node.cmds)
        and all(_adds_plain_column(command) for command in node.cmds)
    ),
}


@dataclass(frozen=True)
class SqlFacts:
    """O que importa ao check num texto de SQL; `statements` conta as instruções não vazias."""

    blocking: bool = False
    concurrent: bool = False
    statements: int = 0
    # None no lugar de um nome que o check não resolve.
    created_concurrent_indexes: list[IndexName | None] = field(default_factory=list)
    dropped_concurrent_indexes: list[IndexName | None] = field(default_factory=list)


def analyze_sql(sql: str) -> SqlFacts:
    """Lê cada instrução do texto e junta o que elas fazem."""
    try:
        statements = split(sql, with_parser=False)
    except ParseError:
        # Nem o scanner aceita (texto entre aspas sem fechar): o servidor também recusaria.
        return SqlFacts(True, True, 1, [None], [])
    facts = [_statement_facts(statement) for statement in statements]
    if _code_tokens(sql) > sum(_code_tokens(statement) for statement in statements):
        # O split sem parser descarta a instrução que não começa por palavra-chave (`foo;`, o
        # PLACEHOLDER de um trecho que o check não montou): o que sobrou não foi lido.
        facts.append(_unparsed_facts(sql))
    return SqlFacts(
        blocking=any(fact.blocking for fact in facts),
        concurrent=any(fact.concurrent for fact in facts),
        statements=len(facts),
        created_concurrent_indexes=[n for f in facts for n in f.created_concurrent_indexes],
        dropped_concurrent_indexes=[n for f in facts for n in f.dropped_concurrent_indexes],
    )


def _code_tokens(sql: str) -> int:
    """Tokens que são código: sem comentários e sem o `;` que separa as instruções."""
    return sum(token.name not in NON_CODE_TOKENS for token in scan(sql))


def _statement_facts(statement: str) -> SqlFacts:
    try:
        (raw,) = parse_sql(statement)
    except ParseError:
        return _unparsed_facts(statement)
    node = raw.stmt
    if node is None:
        return _unparsed_facts(statement)
    return SqlFacts(
        blocking=not _is_safe(node),
        concurrent=_is_concurrent(node),
        statements=1,
        created_concurrent_indexes=_created_concurrent_indexes(node),
        dropped_concurrent_indexes=_dropped_concurrent_indexes(node),
    )


def _unparsed_facts(statement: str) -> SqlFacts:
    """Instrução que o parser recusa: bloqueante, e as palavras-chave decidem o CONCURRENTLY."""
    keywords = [token.name for token in scan(statement)]
    concurrent = "CONCURRENTLY" in keywords and keywords[:1] != ["REFRESH"]
    creates_index = concurrent and "CREATE" in keywords and "INDEX" in keywords
    return SqlFacts(True, concurrent, 1, [None] if creates_index else [], [])


def _is_safe(node: ast.Node) -> bool:
    condition = SAFE_STATEMENTS.get(type(node))
    return condition is not None and condition(node)


def _adds_plain_column(command: ast.AlterTableCmd) -> bool:
    """ADD COLUMN anulável de tipo nativo, sem default, constraint, collation, storage nem nada."""
    column = command.def_
    if command.subtype != AlterTableType.AT_AddColumn or not isinstance(column, ast.ColumnDef):
        return False
    return (
        column.typeName is not None
        and _is_builtin_type(column.typeName)
        and all(
            getattr(column, name) in UNSET_VALUES
            for name in column
            if name not in PLAIN_COLUMN_FIELDS
        )
        and all(c.contype == ConstrType.CONSTR_NULL for c in column.constraints or ())
    )


def _is_builtin_type(type_name: ast.TypeName) -> bool:
    names = [name.sval for name in type_name.names or ()]
    if len(names) == QUALIFIED_NAME_PARTS and names[0] == CATALOG_SCHEMA:
        return True
    return len(names) == 1 and names[0] in BUILTIN_TYPES


def _is_concurrent(node: ast.Node) -> bool:
    """CONCURRENTLY que exige autocommit; o de REFRESH MATERIALIZED VIEW roda em transação."""
    match node:
        case ast.IndexStmt():
            return bool(node.concurrent)
        case ast.DropStmt():
            return node.removeType == ObjectType.OBJECT_INDEX and bool(node.concurrent)
        case ast.ReindexStmt():
            return _has_option(node.params, "concurrently")
        case ast.AlterTableStmt():
            return any(
                _detaches(command)
                and isinstance(command.def_, ast.PartitionCmd)
                and bool(command.def_.concurrent)
                for command in node.cmds or ()
            )
    return False


def _created_concurrent_indexes(node: ast.Node) -> list[IndexName | None]:
    """O índice criado fica no schema da tabela do ON: `(schema, nome)` ou `(nome,)`."""
    if not isinstance(node, ast.IndexStmt) or not node.concurrent:
        return []
    if node.idxname is None:
        return [None]
    schema = node.relation.schemaname if node.relation is not None else None
    return [_resolved((schema, node.idxname) if schema else (node.idxname,))]


def _dropped_concurrent_indexes(node: ast.Node) -> list[IndexName | None]:
    """Só `DROP INDEX CONCURRENTLY IF EXISTS` serve de limpeza antes de um retry."""
    if not (
        isinstance(node, ast.DropStmt)
        and node.removeType == ObjectType.OBJECT_INDEX
        and node.concurrent
        and node.missing_ok
    ):
        return []
    return [_resolved(tuple(part.sval for part in name)) for name in node.objects or ()]


def _resolved(name: IndexName) -> IndexName | None:
    return None if any(PLACEHOLDER in part for part in name) else name


def _has_option(options: tuple[ast.DefElem, ...] | None, name: str) -> bool:
    """A opção está ligada: presente sem valor ou com valor diferente de false/off/0."""
    for option in options or ():
        if option.defname == name:
            value = getattr(option.arg, "sval", None) or getattr(option.arg, "ival", None)
            return str(value).lower() not in {"false", "off", "0"} if value is not None else True
    return False


def _detaches(command: ast.AlterTableCmd) -> bool:
    return command.subtype == AlterTableType.AT_DetachPartition
