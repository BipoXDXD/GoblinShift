"""Nenhum log recebe a exceção crua (docs/GoblinShiftPlan.md, 3.7; #442).

O filtro `omit_exception_detail` tira a mensagem do traceback, mas não vê os campos do evento: um
`fields["error"] = str(error)` levaria nome de arquivo, valor de parâmetro ou o `DETAIL` do
PostgreSQL para o log. A regra é verificada pela árvore sintática de `src/goblinshift`: onde uma
exceção está em escopo, ela só chega ao log por um redutor que devolve tipo ou código.

A segunda regra: toda chave literal de campo de evento está em `LOGGED_FIELDS`, a allowlist do
`JsonFormatter`. Uma chave que falte nela teria o texto descartado em produção sem aviso.

O executor-ctl (`deploy/executor_ctl`) fica fora do escopo: ele é tratado na Tarefa 6 do plano
`2026-11-21-logs-telemetria`.

Limites do verificador: um alias (`failure = error`, e depois `failure` no log) não é rastreado; e
`getattr(error, "orig", error)` cai na própria exceção quando não há `orig`, o que só é seguro
porque o `orig` só é aceito como objeto de um atributo seguro (`getattr(…, "sqlstate", None)`).
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

from goblinshift.entrypoints import maintenance as maintenance_entrypoint
from goblinshift.files.storage import FileStorage
from goblinshift.platform.telemetry import _MESSAGE_SAFE_ERRORS, LOGGED_FIELDS
from goblinshift.runs.worker import coordinator as coordinator_module
from goblinshift.runs.worker.claim import ClaimedAttempt
from goblinshift.runs.worker.coordinator import Coordinator, CoordinatorConfig
from goblinshift.runs.worker.executor_ctl_runner import ExecutorCtlError
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
        # `<campo>:<tipo>` do `ValidationError`, sem o valor (`test_describe_config_error_…`).
        "describe_config_error",
        # Só o código cru do driver (`platform.db.sqlstate_of`), nunca a mensagem.
        "sqlstate_of",
    }
)
# Atributos que guardam número, código ou posição, montados pelo próprio GoblinShift. O `code` não
# entra: o do executor-ctl vem do corpo da resposta e só sai pelo filtro de `exception_fields`.
SAFE_ATTRIBUTES = frozenset({"errno", "position", "alert", "sqlstate"})
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
    """Chamada a redutor ou atributo seguro, direto ou por `getattr`.

    O `orig` (a exceção do DBAPI) não é redutor: só passa como objeto de um atributo seguro, como
    em `getattr(getattr(error, "orig", None), "sqlstate", None)`, que é reduzido por fora.
    """
    match node:
        case ast.Call(func=ast.Name(id=function) | ast.Attribute(attr=function)) if (
            function in REDUCERS
        ):
            return True
        case ast.Call(
            func=ast.Name(id="getattr"), args=[_, ast.Constant(value=str(attribute)), *_]
        ):
            return attribute in SAFE_ATTRIBUTES
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
        _handler('fields = {"orig": str(getattr(error, "orig", None))}'),
        _handler('logger.warning("x: %s", getattr(error, "orig", None))'),
        _handler('logger.warning("x", extra={"fields": {"reason": error.code}})'),
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
        "str-do-orig",
        "orig-no-log",
        "code-cru",
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
        _handler('fields = {"sqlstate": sqlstate_of(error)}'),
        _handler(
            'logger.error("x", extra={"fields": {"config_errors": describe_config_error(error)}})',
            caught="ValidationError",
        ),
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
        "sqlstate-of",
        "campos-do-erro-de-configuracao",
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


def logged_keys(source: str) -> Iterator[tuple[str, int]]:
    """Chaves literais que chegam a `fields` de um evento, com a linha de cada uma.

    Coleta, de dict literal ou de `dict(k=…)`: (a) o valor de `"fields"` em `extra=`; (b) o que
    se atribui (`=`, `|=`, anotado) a nomes que terminam em `fields`, ou se passa ao `update` deles;
    (c) `NOME["k"] = …` com `NOME` terminando em `fields`; (d) o que `_ids` e `*_fields` devolvem e
    as constantes de módulo `*_EVENT`, que são espalhadas em `fields`. Chave montada em tempo de
    execução, ou dict guardado num nome de outro sufixo, não é vista: o formatter só a deixa sair
    com nome de identificador e valor numérico.
    """
    tree = ast.parse(source)
    for node in ast.walk(tree):
        match node:
            case ast.keyword(arg="extra", value=ast.Dict() as extra):
                for extra_key, extra_value in zip(extra.keys, extra.values, strict=True):
                    if _is_constant(extra_key, "fields"):
                        yield from _dict_keys(extra_value)
            case ast.Assign(targets=targets, value=value) if any(
                _names_fields(target) for target in targets
            ):
                yield from _dict_keys(value)
            case (
                ast.AnnAssign(target=target, value=ast.expr() as value)
                | ast.AugAssign(target=target, op=ast.BitOr(), value=value)
            ) if _names_fields(target):
                yield from _dict_keys(value)
            case ast.Call(func=ast.Attribute(value=target, attr="update")) if _names_fields(target):
                yield from _call_keys(node)
            case ast.Assign(targets=targets):
                for assigned in targets:
                    match assigned:
                        case ast.Subscript(value=base, slice=ast.Constant(value=str(literal))) if (
                            _names_fields(base)
                        ):
                            yield literal, assigned.lineno
            case ast.FunctionDef(name=name) if name == "_ids" or name.endswith("_fields"):
                for inner in ast.walk(node):
                    if isinstance(inner, ast.Return):
                        yield from _dict_keys(inner.value)
    for statement in tree.body:
        match statement:
            case ast.Assign(targets=[ast.Name(id=name)], value=value) if name.endswith("_EVENT"):
                yield from _dict_keys(value)


def _names_fields(target: ast.expr) -> bool:
    """`fields`, `*_fields` ou `self.fields`, sem subscrito."""
    match target:
        case ast.Name(id=name) | ast.Attribute(attr=name):
            return name.endswith("fields")
    return False


def _is_constant(node: ast.expr | None, value: str) -> bool:
    return isinstance(node, ast.Constant) and node.value == value


def _dict_keys(node: ast.expr | None) -> Iterator[tuple[str, int]]:
    """As chaves literais de `{…}` ou de `dict(…)`; outra expressão não tem chave a ver."""
    match node:
        case ast.Dict(keys=keys):
            for key in keys:
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    yield key.value, key.lineno
        case ast.Call(func=ast.Name(id="dict")):
            yield from _call_keys(node)


def _call_keys(call: ast.Call) -> Iterator[tuple[str, int]]:
    """As chaves de `dict(…)` ou `update(…)`: o dict literal posicional e os argumentos nomeados."""
    for argument in call.args:
        yield from _dict_keys(argument)
    for keyword in call.keywords:
        if keyword.arg is not None:
            yield keyword.arg, keyword.lineno


@pytest.mark.parametrize(
    "source",
    [
        'logger.info("x", extra={"fields": {"k": 1}})',
        'fields = {"k": 1}',
        'run_fields: dict[str, object] = {"k": 1}',
        'self.fields |= {"k": 1}',
        'fields["k"] = 1',
        'def _ids(c):\n    return {"k": 1}',
        'def _error_fields(e):\n    return {"k": 1}',
        '_PROBE_EVENT = {"k": 1}',
        'fields.update({"k": 1})',
        "fields.update(k=1)",
        "fields = dict(k=1)",
        'logger.info("x", extra={"fields": dict(k=1)})',
        "def _ids(c):\n    return dict(k=1)",
    ],
    ids=[
        "extra",
        "atribuicao",
        "anotada",
        "uniao",
        "subscrito",
        "ids",
        "funcao-fields",
        "constante-event",
        "update-com-dict",
        "update-com-keyword",
        "dict-com-keyword",
        "extra-com-dict",
        "ids-com-dict",
    ],
)
def test_coletor_acha_a_chave(source: str) -> None:
    assert [key for key, _ in logged_keys(source)] == ["k"]


@pytest.mark.parametrize(
    "source",
    [
        'headers = {"k": 1}',
        "fields = {**_ids(c)}",
        "fields[name] = 1",
        'def _parse(c):\n    return {"k": 1}',
        'def f():\n    _LOCAL_EVENT = {"k": 1}',
        'logger.info("x", extra={"other": {"k": 1}})',
    ],
    ids=[
        "outro-nome",
        "espalhamento",
        "chave-variavel",
        "outra-funcao",
        "event-local",
        "extra-sem-fields",
    ],
)
def test_coletor_ignora_o_que_nao_e_campo_de_log(source: str) -> None:
    assert list(logged_keys(source)) == []


def test_toda_chave_literal_de_log_esta_na_allowlist() -> None:
    modules = sorted(SOURCE.rglob("*.py"))
    assert modules, f"nenhum módulo em {SOURCE}"

    outside = [
        f"{key} ({module.relative_to(ROOT)}:{line})"
        for module in modules
        for key, line in logged_keys(module.read_text(encoding="utf-8"))
        if key not in LOGGED_FIELDS
    ]

    assert outside == [], (
        "chave de log fora de `LOGGED_FIELDS`: o formatter descarta o texto dela. Revise se ela "
        "nunca carrega texto livre e acrescente-a à allowlist: " + ", ".join(outside)
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


def test_codigo_do_executor_ctl_fora_do_contrato_nao_vai_para_o_log(
    listen: Callable[[logging.Logger], io.StringIO], canary: str
) -> None:
    stream = listen(maintenance_entrypoint.logger)

    def refuse(_image: str) -> bool:
        # O `code` vem do corpo da resposta do executor-ctl: qualquer texto chega até aqui.
        raise ExecutorCtlError(canary, canary)

    maintenance_entrypoint.ExecutorImageProbe(refuse, "executor@sha256:" + "0" * 64).run_cycle()

    output = stream.getvalue()
    assert canary not in output
    [line] = output.splitlines()
    entry: dict[str, Any] = json.loads(line)
    assert entry["error_type"] == "ExecutorCtlError"
    assert "error_reason" not in entry
