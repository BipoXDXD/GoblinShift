"""O que um texto de SQL de migration faz quanto a locks, lido pelo parser do PostgreSQL (ADR 057).

O pglast embute o parser do próprio PostgreSQL (libpg_query), então texto entre aspas, dollar
quoting, comentários e nomes com aspas seguem as regras do servidor, sem expressão regular.
O texto é dividido em instruções pelo scanner e cada uma é lida à parte: uma instrução que não
parseia (valor desconhecido no lugar de uma palavra-chave, por exemplo) não esconde as outras.

Nega por padrão: só passa sem a marca a instrução cujo tipo de nó está em `SAFE_STATEMENTS` e
cumpre a condição dele (CREATE INDEX só com CONCURRENTLY, ALTER TABLE só com subcomandos de
`SAFE_ALTER_TABLE_COMMANDS` na forma segura etc.). Nó novo ou desconhecido pede a marca, mesmo
que não trave nada; quem o conhece decide e escreve o motivo.

Na dúvida o resultado peca pelo lado seguro: instrução que não parseia pede a marca, CONCURRENTLY
no meio dela conta, e índice criado sem nome resolvido nunca conta como apagado.
"""

import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

from pglast import ast, parse_sql, split
from pglast.enums import AlterTableType, ConstrType, ObjectType
from pglast.parser import ParseError, scan

# Valor desconhecido na montagem do SQL (variável, campo de f-string): vira um identificador.
PLACEHOLDER = "__unresolved__"
# Nome de índice em partes (schema, nome), como o PostgreSQL o guarda.
type IndexName = tuple[str, ...]

VOLATILE_FUNCTION = re.compile(
    r"gen_random_uuid|uuid_generate_v\w+|uuidv\d|random|clock_timestamp|timeofday|nextval"
)
SERIAL_TYPES = frozenset({"smallserial", "serial", "bigserial", "serial2", "serial4", "serial8"})
# Constraints validadas na hora: a forma segura é NOT VALID e VALIDATE depois.
VALIDATED_CONSTRAINTS = frozenset(
    {ConstrType.CONSTR_FOREIGN, ConstrType.CONSTR_CHECK, ConstrType.CONSTR_NOTNULL}
)
# Constraints que constroem índice sob lock, salvo `USING INDEX` de um índice já pronto.
INDEX_CONSTRAINTS = frozenset(
    {ConstrType.CONSTR_UNIQUE, ConstrType.CONSTR_PRIMARY, ConstrType.CONSTR_EXCLUSION}
)
# Constraints de coluna nova que constroem índice, varrem a tabela ou a reescrevem.
REWRITING_COLUMN_CONSTRAINTS = frozenset(
    {
        ConstrType.CONSTR_UNIQUE,
        ConstrType.CONSTR_PRIMARY,
        ConstrType.CONSTR_FOREIGN,
        ConstrType.CONSTR_CHECK,
        ConstrType.CONSTR_IDENTITY,
    }
)
STORED = "s"

# A allowlist: tipo de nó → condição em que ele não reescreve nem varre tabela em uso sob lock.
SAFE_STATEMENTS: dict[type[Any], Callable[[Any], bool]] = {
    # DML não é DDL: trava linhas, não a tabela.
    ast.SelectStmt: lambda _: True,
    ast.InsertStmt: lambda _: True,
    ast.UpdateStmt: lambda _: True,
    ast.DeleteStmt: lambda _: True,
    ast.CommentStmt: lambda _: True,
    # Tabela nova; PARTITION OF e INHERITS travam a tabela-mãe.
    ast.CreateStmt: lambda node: not node.partbound and not node.inhRelations,
    ast.CreateSchemaStmt: lambda node: all(_is_safe(item) for item in node.schemaElts or ()),
    ast.IndexStmt: lambda node: bool(node.concurrent),
    ast.DropStmt: lambda node: node.removeType == ObjectType.OBJECT_INDEX and bool(node.concurrent),
    ast.ReindexStmt: lambda node: _has_option(node.params, "concurrently"),
    ast.RefreshMatViewStmt: lambda node: bool(node.concurrent),
    ast.VacuumStmt: lambda node: not _has_option(node.options, "full"),
    # Só ADD VALUE; RENAME VALUE quebra quem ainda grava o nome antigo.
    ast.AlterEnumStmt: lambda node: node.oldVal is None,
    ast.AlterTableStmt: lambda node: all(_alter_is_safe(command) for command in node.cmds or ()),
}
# Subcomandos de ALTER TABLE que só mexem no catálogo, na forma segura de cada um.
SAFE_ALTER_TABLE_COMMANDS: dict[AlterTableType, Callable[[Any], bool]] = {
    AlterTableType.AT_AddColumn: lambda command: not _column_is_blocking(command.def_),
    AlterTableType.AT_DropColumn: lambda _: True,
    AlterTableType.AT_ColumnDefault: lambda _: True,
    AlterTableType.AT_DropNotNull: lambda _: True,
    AlterTableType.AT_AddConstraint: lambda command: _constraint_is_safe(command.def_),
    AlterTableType.AT_ValidateConstraint: lambda _: True,
    AlterTableType.AT_DropConstraint: lambda _: True,
    AlterTableType.AT_DetachPartition: lambda command: bool(command.def_.concurrent),
    # Conclui um DETACH CONCURRENTLY interrompido.
    AlterTableType.AT_DetachPartitionFinalize: lambda _: True,
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
    return SqlFacts(
        blocking=any(fact.blocking for fact in facts),
        concurrent=any(fact.concurrent for fact in facts),
        statements=len(facts),
        created_concurrent_indexes=[n for f in facts for n in f.created_concurrent_indexes],
        dropped_concurrent_indexes=[n for f in facts for n in f.dropped_concurrent_indexes],
    )


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


def _alter_is_safe(command: ast.AlterTableCmd) -> bool:
    if command.subtype is None:
        return False
    condition = SAFE_ALTER_TABLE_COMMANDS.get(command.subtype)
    return condition is not None and condition(command)


def _constraint_is_safe(constraint: ast.Constraint) -> bool:
    """FK, CHECK e NOT NULL com NOT VALID; UNIQUE, PK e EXCLUDE só com `USING INDEX`."""
    if constraint.contype in VALIDATED_CONSTRAINTS:
        return bool(constraint.skip_validation)
    if constraint.contype in INDEX_CONSTRAINTS:
        return constraint.indexname is not None
    return False


def _column_is_blocking(column: ast.ColumnDef) -> bool:
    """Coluna nova que reescreve a tabela, a varre para validar ou constrói índice sob lock."""
    if column.typeName is not None:
        type_name = _last_name(column.typeName.names)
        # Tipo que o check não resolve pode ser serial ou exigir reescrita.
        if type_name in SERIAL_TYPES or PLACEHOLDER in type_name:
            return True
    for constraint in column.constraints or ():
        if constraint.contype in REWRITING_COLUMN_CONSTRAINTS:
            return True
        # Coluna gerada VIRTUAL (padrão no PostgreSQL 18) não é gravada; STORED reescreve.
        if (
            constraint.contype == ConstrType.CONSTR_GENERATED
            and constraint.generated_kind == STORED
        ):
            return True
        if constraint.contype == ConstrType.CONSTR_DEFAULT and _is_volatile(constraint.raw_expr):
            return True
    return False


def _is_volatile(expression: ast.Node | None) -> bool:
    """O default chama função volátil, ou tem trecho que o check não resolve."""
    return any(
        _calls_volatile(node) or _is_unresolved_reference(node)
        for node in (_walk(expression) if expression is not None else ())
    )


def _calls_volatile(node: ast.Node) -> bool:
    if not isinstance(node, ast.FuncCall):
        return False
    name = _last_name(node.funcname)
    return VOLATILE_FUNCTION.fullmatch(name) is not None or PLACEHOLDER in name


def _is_unresolved_reference(node: ast.Node) -> bool:
    """Valor montado no lugar de uma expressão: o PostgreSQL não aceita coluna num DEFAULT."""
    return isinstance(node, ast.ColumnRef) and any(
        PLACEHOLDER in (getattr(field, "sval", None) or "") for field in node.fields or ()
    )


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


def _last_name(names: tuple[ast.String, ...] | None) -> str:
    """Última parte de um nome qualificado, em minúsculas; vazio sem nome."""
    return (names[-1].sval or "").lower() if names else ""


def _walk(node: ast.Node) -> Iterator[ast.Node]:
    yield node
    for attribute in node:
        child = getattr(node, attribute)
        children = child if isinstance(child, tuple) else (child,)
        for item in children:
            if isinstance(item, ast.Node):
                yield from _walk(item)
