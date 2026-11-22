"""Nenhum log recebe a exceção crua (docs/GoblinShiftPlan.md, 3.7; #442).

O filtro `omit_exception_detail` tira a mensagem do traceback, mas não vê os campos do evento: um
`fields["error"] = str(error)` levaria nome de arquivo, valor de parâmetro ou o `DETAIL` do
PostgreSQL para o log. A regra é verificada pela árvore sintática de `src/goblinshift`: onde uma
exceção está em escopo, ela só chega ao log por um redutor que devolve tipo ou código.

O executor-ctl (`deploy/executor_ctl`) fica fora do escopo: ele é tratado na Tarefa 6 do plano
`2026-11-21-logs-telemetria`.
"""

import ast
import io
import json
import logging
import uuid
from collections.abc import Callable, Iterable, Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any, cast

import pytest
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.files.storage import FileStorage
from goblinshift.platform.telemetry import _MESSAGE_SAFE_ERRORS
from goblinshift.runs.worker import coordinator as coordinator_module
from goblinshift.runs.worker.claim import ClaimedAttempt
from goblinshift.runs.worker.coordinator import Coordinator, CoordinatorConfig
from goblinshift.runs.worker.runner import Runner, RunnerError

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "src" / "goblinshift"
SAFE_ERRORS = frozenset(error.__name__ for error in _MESSAGE_SAFE_ERRORS)

LOG_METHODS = frozenset({"debug", "info", "warning", "error", "exception", "critical", "log"})
# Funções que devolvem só tipo, SQLSTATE, código ou classificação da exceção, nunca a mensagem.
REDUCERS = frozenset(
    {
        "type",
        "exception_fields",
        "database_sqlstate",
        "is_database_error",
        "_database_error_fields",
        "_error_fields",
        "_failure_kind",
        "_type_and_sqlstate",
        "_describe_failure",
    }
)
# Atributos que guardam código ou posição, montados pelo próprio GoblinShift.
SAFE_ATTRIBUTES = frozenset({"errno", "code", "position", "alert", "sqlstate"})
EXCEPTION_BASE = "BaseException"
EXCEPTION_SUFFIXES = ("Error", "Exception")


def raw_exception_uses(source: str, safe_errors: frozenset[str] = SAFE_ERRORS) -> list[int]:
    """Linhas em que uma exceção em escopo chega a um log ou a campos de evento sem redutor."""
    lines: set[int] = set()
    for name, scope in _exception_scopes(ast.parse(source), safe_errors):
        for sink in _sinks(scope):
            lines.update(_raw_uses(sink, name))
    return sorted(lines)


def _exception_scopes(
    tree: ast.AST, safe_errors: frozenset[str]
) -> Iterator[tuple[str, list[ast.stmt]]]:
    """Cada nome que guarda uma exceção, com as instruções em que ele vale."""
    for node in ast.walk(tree):
        match node:
            case ast.ExceptHandler(name=str(name), type=caught) if not _only_safe(
                caught, safe_errors
            ):
                yield name, node.body
            case ast.FunctionDef() | ast.AsyncFunctionDef():
                for argument in _arguments(node.args):
                    if _names_an_exception(argument.annotation):
                        yield argument.arg, node.body


def _only_safe(caught: ast.expr | None, safe_errors: frozenset[str]) -> bool:
    """Se o `except` só pega erros cuja mensagem é montada com nomes de variável."""
    if caught is None:
        return False
    names = [
        _type_name(item) for item in (caught.elts if isinstance(caught, ast.Tuple) else [caught])
    ]
    return all(name in safe_errors for name in names)


def _type_name(node: ast.expr) -> str | None:
    match node:
        case ast.Name(id=name) | ast.Attribute(attr=name):
            return name
    return None


def _arguments(args: ast.arguments) -> Iterator[ast.arg]:
    yield from args.posonlyargs
    yield from args.args
    yield from args.kwonlyargs
    if args.vararg is not None:
        yield args.vararg
    if args.kwarg is not None:
        yield args.kwarg


def _names_an_exception(annotation: ast.expr | None) -> bool:
    if annotation is None:
        return False
    names = (_type_name(node) for node in ast.walk(annotation) if isinstance(node, ast.expr))
    return any(
        name is not None and (name == EXCEPTION_BASE or name.endswith(EXCEPTION_SUFFIXES))
        for name in names
    )


def _sinks(scope: Iterable[ast.stmt]) -> Iterator[ast.AST]:
    """Expressões que vão para o log: chamadas de log, `extra=` e o que se atribui a `*fields`."""
    for statement in scope:
        for node in ast.walk(statement):
            match node:
                case ast.Call(func=ast.Attribute(attr=method)) if method in LOG_METHODS:
                    yield from node.args
                    yield from (
                        keyword.value
                        for keyword in node.keywords
                        if not (keyword.arg == "exc_info" and isinstance(keyword.value, ast.Name))
                    )
                case ast.Call(func=ast.Attribute(value=target, attr="update")) if _is_fields(
                    target
                ):
                    yield node
                case ast.keyword(arg="extra"):
                    yield node.value
                case ast.Assign(targets=targets) if any(_is_fields(item) for item in targets):
                    yield node.value
                case (
                    ast.AnnAssign(target=target, value=ast.expr() as value)
                    | ast.AugAssign(target=target, value=value)
                ) if _is_fields(target):
                    yield value


def _is_fields(target: ast.expr) -> bool:
    """`fields`, `*_fields`, `fields["chave"]` ou `self.fields`."""
    while isinstance(target, ast.Subscript):
        target = target.value
    match target:
        case ast.Name(id=name) | ast.Attribute(attr=name):
            return name.endswith("fields")
    return False


def _raw_uses(node: ast.AST, name: str) -> Iterator[int]:
    if _is_reduced(node):
        return
    if isinstance(node, ast.Name) and node.id == name:
        yield node.lineno
        return
    for child in ast.iter_child_nodes(node):
        yield from _raw_uses(child, name)


def _is_reduced(node: ast.AST) -> bool:
    """Chamada a redutor, atributo de código (`error.errno`, `error.orig.sqlstate`) ou `getattr`."""
    match node:
        case ast.Call(func=ast.Name(id=function) | ast.Attribute(attr=function)) if (
            function in REDUCERS
        ):
            return True
        case ast.Call(
            func=ast.Name(id="getattr"), args=[_, ast.Constant(value=str(attribute)), *_]
        ):
            # `orig` é a exceção do DBAPI, que sai do log só pelo `sqlstate` lido dela.
            return attribute in SAFE_ATTRIBUTES or attribute == "orig"
        case ast.Attribute(attr=attribute):
            return attribute in SAFE_ATTRIBUTES
    return False


def _handler(body: str, caught: str = "OSError") -> str:
    """Um `except` cujo corpo (uma linha) está na linha 4."""
    return f"try:\n    pass\nexcept {caught} as error:\n    {body}\n"


@pytest.mark.parametrize(
    "source",
    [
        _handler('logger.warning("x: %s", error)'),
        _handler('logger.warning("x", extra={"fields": {"error": str(error)}})'),
        _handler('foo(extra={"e": repr(error)})'),
        _handler('fields = {"e": f"{error}"}'),
        _handler('run_fields = {"e": error.args}'),
        _handler("fields = _ids(claimed, error)"),
        _handler("fields.update(e=str(error))"),
        _handler('logger.error("x: %s", error)', caught="(MissingSecretError, OSError)"),
        'def _ids(error: Exception | None) -> None:\n    fields["error"] = str(error)\n',
        'async def f(failure: RunnerError) -> None:\n    logger.info("x", failure.detail)\n',
    ],
    ids=[
        "argumento-de-log",
        "str-em-fields",
        "repr-em-extra",
        "fstring-em-fields",
        "args-em-fields",
        "repassada-a-funcao",
        "update-de-fields",
        "except-misto",
        "parametro-anotado",
        "atributo-livre",
    ],
)
def test_verificador_acusa_a_excecao_crua(source: str) -> None:
    # A última linha de cada fonte é a que leva a exceção ao log.
    assert raw_exception_uses(source) == [source.count("\n")]


@pytest.mark.parametrize(
    "source",
    [
        _handler('logger.warning("x", exc_info=error)'),
        _handler('logger.warning("x", extra={"fields": {**_ids(c), **exception_fields(error)}})'),
        _handler('fields = {"error_type": type(error).__name__, "errno": error.errno}'),
        _handler('fields = {"sqlstate": getattr(getattr(error, "orig", None), "sqlstate", None)}'),
        _handler('fields = {"errno": errno.errorcode.get(getattr(error, "errno", 0))}'),
        _handler('fields = {"sqlstate": getattr(error.orig, "sqlstate", None)}'),
        _handler('logger.error("x: %s", error)', caught="MissingSecretError"),
        _handler("raise PreparationError(str(error)) from error"),
        _handler("message = str(error)"),
        'def f(owner: str) -> None:\n    logger.info("x: %s", owner)\n',
    ],
    ids=[
        "exc-info",
        "exception-fields",
        "tipo-e-errno",
        "sqlstate-do-orig",
        "getattr-de-errno",
        "sqlstate-por-atributo",
        "erro-de-configuracao",
        "raise-sem-log",
        "variavel-fora-de-fields",
        "parametro-sem-excecao",
    ],
)
def test_verificador_aceita_redutores(source: str) -> None:
    assert raw_exception_uses(source) == []


def test_nenhum_log_recebe_a_excecao_crua() -> None:
    modules = sorted(SOURCE.rglob("*.py"))
    assert modules, f"nenhum módulo em {SOURCE}"

    offenders = [
        f"{module.relative_to(ROOT)}:{line}"
        for module in modules
        for line in raw_exception_uses(module.read_text(encoding="utf-8"))
    ]

    assert offenders == [], (
        "exceção crua em log ou campos de evento; use `exception_fields(error)`: "
        + ", ".join(offenders)
    )


class _RefusingRunner:
    """Runner cuja remoção falha com o canário na mensagem."""

    def __init__(self, message: str) -> None:
        self._message = message

    def remove(self, attempt_id: uuid.UUID) -> None:
        raise RunnerError(self._message)


def _claimed() -> ClaimedAttempt:
    return ClaimedAttempt(
        run_id=uuid.uuid4(),
        owner_id=uuid.uuid4(),
        attempt_id=uuid.uuid4(),
        generation=1,
        number=1,
        recipe_id="pdf.merge",
        recipe_version=1,
        executor_image="executor@sha256:" + "0" * 64,
        parameters={},
        input_upload_ids=(),
        traceparent=None,
        queue_wait_ms=0,
    )


def test_exception_fields_no_worker_nao_leva_o_canario(
    listen: Callable[[logging.Logger], io.StringIO], canary: str
) -> None:
    stream = listen(coordinator_module.logger)
    coordinator = Coordinator(
        cast(sessionmaker[Session], None),
        cast(FileStorage, None),
        cast(Runner, _RefusingRunner(canary)),
        CoordinatorConfig(lease=timedelta(seconds=30)),
    )
    claimed = _claimed()

    coordinator._try_remove(claimed)

    output = stream.getvalue()
    assert canary not in output
    [line] = output.splitlines()
    entry: dict[str, Any] = json.loads(line)
    assert entry["error_type"] == "RunnerError"
    assert entry["attempt_id"] == str(claimed.attempt_id)
    assert "error" not in entry
