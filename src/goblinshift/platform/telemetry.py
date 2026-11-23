"""Logs JSON e traces OpenTelemetry (docs/GoblinShiftPlan.md, 3.7).

Os traces só são exportados quando `OTEL_EXPORTER_OTLP_ENDPOINT` está definido; o
endpoint, o nome do serviço e os headers seguem as variáveis padrão do OpenTelemetry.
"""

import contextlib
import errno
import hashlib
import hmac
import ipaddress
import json
import logging
import math
import os
import re
import sys
import threading
import traceback
from collections.abc import Callable, Iterator
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

import psycopg
import sqlalchemy
import sqlalchemy.engine.create
from fastapi import FastAPI
from opentelemetry.context import Context
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.instrumentation.sqlalchemy.engine import EngineTracer
from opentelemetry.instrumentation.utils import unwrap
from opentelemetry.propagate import set_global_textmap
from opentelemetry.propagators.textmap import (
    CarrierT,
    Getter,
    Setter,
    TextMapPropagator,
    default_getter,
    default_setter,
)
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import Event as SdkEvent
from opentelemetry.sdk.trace import ReadableSpan, SpanProcessor, TracerProvider
from opentelemetry.sdk.trace import Span as SdkSpan
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor, SpanExporter
from opentelemetry.sdk.trace.sampling import ALWAYS_ON
from opentelemetry.trace import (
    NoOpTracer,
    Span,
    Status,
    StatusCode,
    Tracer,
    format_span_id,
    format_trace_id,
    get_current_span,
)
from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError

from goblinshift.platform.settings import (
    ExecutorConfigError,
    MissingSecretError,
    Settings,
    WebConfigError,
)

ALERT_ATTRIBUTE_PREFIX = "alert."
UVICORN_ERROR_LOGGER = "uvicorn.error"
_DATABASE_ERRORS = (SQLAlchemyError, psycopg.Error)
# Erros de configuração cujas mensagens são montadas só com nomes de variável de ambiente: a
# mensagem diz o que corrigir e não traz entrada do usuário. Toda outra exceção sai sem ela.
_MESSAGE_SAFE_ERRORS: tuple[type[BaseException], ...] = (
    MissingSecretError,
    ExecutorConfigError,
    WebConfigError,
)
_POOL_LOGGER_PREFIX = "sqlalchemy.pool"
# O repr da conexão traz host, porta, usuário e banco, e o pool o põe no texto das mensagens.
_PSYCOPG_CONNECTION_REPR = re.compile(r"<psycopg\.\w*Connection [^>]*>")
# Classe (2) e condição (3) do PostgreSQL; o que não casar não vai para o log nem para a tela.
_SQLSTATE = re.compile(r"[0-9A-Z]{5}")
_SQLSTATE_ATTRIBUTE = "sqlstate"
# Motivo de contrato (`image_not_allowed`): identificador curto, nunca texto livre.
_ERROR_REASON = re.compile(r"[a-z][a-z0-9_]{0,63}")
# Campos de evento que saem no log com qualquer valor. Cada chave guarda id, contagem, duração,
# código ou nome escolhido pelo GoblinShift, nunca texto vindo do usuário nem a mensagem de uma
# exceção: `error` não entra. Chave fora da lista só sai com número ou booleano, que não carregam
# texto, para as contagens de cada volta (`**counts`) não precisarem ser listadas uma a uma.
# `tests/security/test_log_fitness.py` acusa chave literal de log que falte aqui.
LOGGED_FIELDS = frozenset(
    {
        "absent",
        "accounts",
        "adoptions_refused",
        "alert",
        "attempt",
        "attempt.number",
        "attempt_id",
        "attempt_ids",
        "attempts",
        "checked",
        "cycle",
        "duration_ms",
        "errno",
        "errnos",
        "error.code",
        "error.type",
        "error_reason",
        "error_type",
        "failed",
        "failed_step",
        "failures",
        "generation",
        "held_s",
        "image",
        "items_left",
        "limit_s",
        "live",
        "main",
        "objects",
        "objects_adopted",
        "outcome",
        "pause_s",
        "pools_corrected",
        "position",
        "reason",
        "retry_in_s",
        "run_id",
        "runner",
        "sample",
        "signal",
        "skipped",
        "sqlstate",
        "step",
        "storage_key",
        "tampered",
        "tmp_part",
        "unreadable",
        "upload_id",
        "user.hash",
        "user_id",
    }
)
_DROPPED_FIELDS = "dropped_fields"
# Chave fora de `LOGGED_FIELDS`: o nome também vai ao log, e só um identificador curto passa.
_FIELD_NAME = re.compile(r"[a-z][a-z0-9_.]{0,63}")
# Inteiro maior não é contagem nem duração, o OpenObserve guarda 64 bits e, acima de 4300 dígitos,
# o `json.dumps` levanta e a linha inteira se perde.
_MAX_INT_BITS = 64
# Ids de correlação que o filtro `add_correlation_ids` põe no registro e o formatter copia para a
# linha: o `request_id` liga a linha ao `X-Request-ID` e ao `#ref=` que o usuário reporta; o
# `trace_id` e o `span_id`, ao trace.
_CORRELATION_FIELDS = ("request_id", "trace_id", "span_id")
# Chaves que o próprio formatter escreve: um campo de evento não as sobrescreve. O `sqlstate` não
# está aqui porque também é campo de evento; o do registro, quando há, vence o do campo.
_BASE_FIELDS = frozenset(
    {"time", "level", "logger", "message", "exception", _DROPPED_FIELDS, *_CORRELATION_FIELDS}
)
# O `request_id` da requisição em curso; `RequestContextMiddleware` o define em volta da rota.
_REQUEST_ID: ContextVar[str | None] = ContextVar("goblinshift_request_id", default=None)

logger = logging.getLogger(__name__)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        # Campos do evento estruturado: `logger.info("...", extra={"fields": {...}})`.
        fields = getattr(record, "fields", None)
        dropped = _add_fields(entry, fields) if isinstance(fields, dict) else 0
        sqlstate = getattr(record, _SQLSTATE_ATTRIBUTE, None)
        if sqlstate is not None:
            entry[_SQLSTATE_ATTRIBUTE] = sqlstate
        for name in _CORRELATION_FIELDS:
            value = getattr(record, name, None)
            if value is not None:
                entry[name] = value
        exception = record.exc_info[1] if record.exc_info else None
        if exception is not None:
            # Reduzida aqui também, sem ler `exc_text`: um registro que não passou pelo filtro, ou
            # que um handler comum formatou antes, traria nele o traceback com a mensagem.
            entry["exception"] = _reduced_traceback(exception)
        if dropped:
            entry[_DROPPED_FIELDS] = dropped
        return json.dumps(entry, ensure_ascii=False)


def _add_fields(entry: dict[str, Any], fields: dict[Any, object]) -> int:
    """Põe em `entry` os campos que podem ir ao log e devolve quantos caíram."""
    dropped = 0
    for key, value in fields.items():
        if _is_loggable(key, value):
            entry[key] = value
        else:
            dropped += 1
    return dropped


def _is_loggable(key: object, value: object) -> bool:
    """A chave de `LOGGED_FIELDS` passa com qualquer valor; a de fora, com nome de identificador e
    número ou booleano. Nunca passa o que sobrescreveria a própria linha (`message`, `level`, …)
    nem número que o JSON não representa: `nan`, `inf` ou inteiro fora de 64 bits."""
    if key in _BASE_FIELDS or not _is_json_number(value):
        return False
    if key in LOGGED_FIELDS:
        return True
    return (
        isinstance(key, str)
        and _FIELD_NAME.fullmatch(key) is not None
        and isinstance(value, (bool, int, float))
    )


def _is_json_number(value: object) -> bool:
    """Falso só para número sem representação no log; o que não é número passa."""
    match value:
        case bool():
            return True
        case int():
            return value.bit_length() <= _MAX_INT_BITS
        case float():
            return math.isfinite(value)
    return True


@contextlib.contextmanager
def request_id_bound(request_id: str) -> Iterator[None]:
    """Toda linha de log escrita dentro do bloco leva este `request_id`; ao sair, volta o
    anterior."""
    token = _REQUEST_ID.set(request_id)
    try:
        yield
    finally:
        _REQUEST_ID.reset(token)


def add_correlation_ids(record: logging.LogRecord) -> bool:
    """Filtro de log: põe no registro o `request_id` da requisição e o trace e o span correntes.

    Cada id só entra quando existe; fora de requisição e de span a linha sai sem eles. Os três são
    sempre reescritos, para um `extra={"request_id": ...}` de quem registra não chegar à linha.
    """
    record.request_id = _REQUEST_ID.get()
    context = get_current_span().get_span_context()
    record.trace_id = format_trace_id(context.trace_id) if context.is_valid else None
    record.span_id = format_span_id(context.span_id) if context.is_valid else None
    return True


def omit_exception_detail(record: logging.LogRecord) -> bool:
    """Filtro de log: toda exceção sai só com tipos e frames, de banco ou não.

    A mensagem de uma exceção pode trazer entrada do usuário: nome de arquivo, valor de parâmetro,
    trecho de documento e, nos erros do SQLAlchemy e do psycopg, host, SQL e o `DETAIL` do
    PostgreSQL (3.7). O local da falha vem dos frames, e o tipo de cada exceção da cadeia diz o
    que houve. O texto vai em `exc_text`, que os formatadores usam no lugar de formatar `exc_info`:
    o registro continua com a exceção. O SQLSTATE do PostgreSQL, que é só um código, fica junto do
    tipo e no atributo `sqlstate` do registro. Só os erros de configuração de
    `_MESSAGE_SAFE_ERRORS`, com mensagens montadas com nomes de variável, saem com a mensagem. As
    mensagens do pool perdem o repr da conexão.
    """
    if record.name.startswith(_POOL_LOGGER_PREFIX):
        record.msg = _PSYCOPG_CONNECTION_REPR.sub("<psycopg connection>", record.getMessage())
        record.args = None
    exception = record.exc_info[1] if record.exc_info else None
    if exception is None:
        return True
    record.exc_text = _reduced_traceback(exception)
    sqlstate = database_sqlstate(exception)
    if sqlstate is not None:
        setattr(record, _SQLSTATE_ATTRIBUTE, sqlstate)
    return True


def exception_fields(error: BaseException) -> dict[str, str]:
    """Campos de evento que descrevem uma exceção sem a mensagem dela.

    O tipo, o SQLSTATE do banco, o nome do `errno` (`ENOSPC`, `EIO`) e o `code` de contrato
    (`ExecutorCtlError.code`) quando ele tem a forma de identificador: um `code` com outra forma
    pode trazer texto livre e fica de fora.
    """
    fields = {"error_type": type(error).__name__}
    sqlstate = database_sqlstate(error)
    if sqlstate is not None:
        fields[_SQLSTATE_ATTRIBUTE] = sqlstate
    number = getattr(error, "errno", None)
    errno_name = errno.errorcode.get(number) if isinstance(number, int) else None
    if errno_name is not None:
        fields["errno"] = errno_name
    code = getattr(error, "code", None)
    if isinstance(code, str) and _ERROR_REASON.fullmatch(code):
        fields["error_reason"] = code
    return fields


def is_database_error(exception: BaseException) -> bool:
    """Se há erro do SQLAlchemy ou do psycopg na cadeia que o traceback mostraria."""
    return any(isinstance(item, _DATABASE_ERRORS) for item in _exception_chain(exception))


def database_sqlstate(exception: BaseException) -> str | None:
    """O SQLSTATE do primeiro erro do psycopg na cadeia, ou `None` se não houver um bem formado.

    Só o código sai daqui, nunca a mensagem: ela pode trazer valores do usuário no `DETAIL`.
    """
    for item in _exception_chain(exception):
        if isinstance(item, psycopg.Error):
            return _well_formed_sqlstate(item)
    return None


def _well_formed_sqlstate(error: psycopg.Error) -> str | None:
    sqlstate = error.sqlstate
    if sqlstate is None or not _SQLSTATE.fullmatch(sqlstate):
        return None
    return sqlstate


def _exception_chain(exception: BaseException) -> Iterator[BaseException]:
    """A exceção e as que o traceback mostraria: causa, contexto e membros de grupo."""
    pending = [exception]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        yield current
        linked = current.__cause__ if current.__suppress_context__ else current.__context__
        if linked is not None:
            pending.append(linked)
        if isinstance(current, BaseExceptionGroup):
            pending.extend(current.exceptions)


def _reduced_traceback(exception: BaseException) -> str:
    """Tipos e frames de toda a cadeia da exceção, sem mensagens (ver `omit_exception_detail`)."""
    return "\n\n".join(_type_and_frames(item) for item in _exception_chain(exception))


def _type_and_frames(exception: BaseException) -> str:
    frames = "".join(traceback.format_tb(exception.__traceback__))
    return f"Traceback (most recent call last):\n{frames}{_described(exception)}"


def _described(exception: BaseException) -> str:
    """O tipo da exceção; a mensagem só nos erros de `_MESSAGE_SAFE_ERRORS`.

    Nunca chama `str()` nem lê `__notes__` das outras: as duas coisas podem trazer entrada do
    usuário, e um `__str__` quebrado não pode derrubar a linha de log.
    """
    # Tipo exato: uma subclasse pode montar a mensagem com entrada do usuário.
    if type(exception) in _MESSAGE_SAFE_ERRORS:
        return "".join(traceback.format_exception_only(exception)).rstrip("\n")
    kind = type(exception)
    described = f"{kind.__module__}.{kind.__qualname__}"
    if isinstance(exception, psycopg.Error):
        sqlstate = _well_formed_sqlstate(exception)
        if sqlstate is not None:
            described = f"{described} (SQLSTATE {sqlstate})"
    return described


class AlertEventCounter(logging.Handler):
    """Soma cada evento `alert=` do log no atributo `alert.<evento>` do span corrente.

    Os logs não são exportados: o alerta de causa lê o atributo no stream de traces. Só o nome e a
    contagem saem; os campos do evento (chaves, IDs) ficam no log. Sem span gravando, como numa CLI
    ou com os traces desligados, o evento fica só no log. Uma falha aqui não chega a quem registrou.
    """

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._count(record)
        except Exception:
            self.handleError(record)

    def _count(self, record: logging.LogRecord) -> None:
        fields = getattr(record, "fields", None)
        event = fields.get("alert") if isinstance(fields, dict) else None
        span = get_current_span()
        if event is None or not span.is_recording():
            return
        attribute = f"{ALERT_ATTRIBUTE_PREFIX}{event}"
        present = getattr(span, "attributes", None) or {}
        span.set_attribute(attribute, present.get(attribute, 0) + 1)


def configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    handler.addFilter(omit_exception_detail)
    handler.addFilter(add_correlation_ids)
    logging.basicConfig(level=logging.INFO, handlers=[handler, AlertEventCounter()], force=True)
    # O Uvicorn escreve por handlers próprios, fora do root (`propagate: False`).
    uvicorn_error = logging.getLogger(UVICORN_ERROR_LOGGER)
    uvicorn_error.addFilter(omit_exception_detail)
    uvicorn_error.addFilter(add_correlation_ids)


# Prefixos que sobram do IP do cliente no evento: o bastante para ver rede ou provedor, nunca a
# pessoa (3.7: "nunca entram em evento: ... IP completo").
IPV4_PREFIX_BITS = 24
IPV6_PREFIX_BITS = 48
UNKNOWN_ADDRESS = "unknown"
# Atributos que a instrumentação ASGI preenche com a URL completa, query string incluída, ou com
# o IP do cliente; nomes antigos e novos das convenções semânticas.
_URL_ATTRIBUTES = ("http.url", "url.full")
_TARGET_ATTRIBUTES = ("http.target", "url.path")
# Template da rota que a instrumentação ASGI grava antes do hook, também no casamento parcial
# (405); sem rota, este valor fica no lugar do caminho.
_ROUTE_ATTRIBUTE = "http.route"
UNMATCHED_ROUTE = "unmatched"
# Preenchidos com o header `Host`, que o cliente escreve como quiser.
_SERVER_NAME_ATTRIBUTES = ("http.server_name", "server.address")
_QUERY_ATTRIBUTES = ("url.query",)
_CLIENT_ADDRESS_ATTRIBUTES = ("net.peer.ip", "client.address", "http.client_ip")
_SQLALCHEMY_SCOPE = "opentelemetry.instrumentation.sqlalchemy"
REDACTED_DATABASE_ERROR = "database error (detail redacted)"
_EXCEPTION_EVENT = "exception"
_EXCEPTION_DETAIL_ATTRIBUTES = ("exception.message", "exception.stacktrace")
# Header que o cliente escreve como quiser: nenhum header entra no evento (3.7).
_USER_AGENT_ATTRIBUTES = ("http.user_agent", "user_agent.original")


class UserHasher:
    """`user.hash` dos eventos: HMAC-SHA256 do `owner_id` com segredo do servidor (3.7).

    Agrupa eventos por conta sem guardar o ID: sem o segredo, o hash não volta ao `owner_id`.
    """

    def __init__(self, key: bytes) -> None:
        self._key = key

    def hash(self, owner_id: UUID) -> str:
        return hmac.new(self._key, owner_id.bytes, hashlib.sha256).hexdigest()


def truncate_client_address(address: str) -> str:
    """IP do cliente sem os bits que identificam a máquina; o que não é IP vira `unknown`."""
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return UNKNOWN_ADDRESS
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    bits = IPV4_PREFIX_BITS if isinstance(ip, ipaddress.IPv4Address) else IPV6_PREFIX_BITS
    return str(ipaddress.ip_network(f"{ip}/{bits}", strict=False).network_address)


REDACTED_EXCEPTION_DETAIL = "exception detail redacted"
_EXCEPTION_EVENT_NAME = "exception"
# O que resta do evento: o tipo, que agrupa as falhas, e `exception.escaped`, que o SDK grava sempre
# como "False", mesmo quando a exceção escapa, e fica só para o evento manter a forma da convenção.
_EXCEPTION_ATTRIBUTES_KEPT = ("exception.type", "exception.escaped")


def _without_main_exception_detail(span: ReadableSpan) -> ReadableSpan:
    """Tira do span `main` o detalhe da exceção de uma resposta já começada.

    Sem resposta iniciada, o `RequestContextMiddleware` troca a exceção por um 500 e ela não chega
    à instrumentação. Com o status já enviado ela sobe, e a instrumentação grava `str(exceção)` e a
    pilha no evento `exception` e no status; no SQLAlchemy, `str()` traz host, SQL e parâmetros.
    O span terminado é imutável, então sai uma cópia.

    O status só é trocado quando há evento `exception`, o que preserva o texto fixo do ciclo de
    manutenção. Limite: com `OTEL_SPAN_EVENT_COUNT_LIMIT=0`, que não configuramos, não há evento
    e o `str(exceção)` ficaria no status.
    """
    is_main = (span.attributes or {}).get("main") is True
    if not is_main or not any(event.name == _EXCEPTION_EVENT_NAME for event in span.events):
        return span
    failed = span.status.status_code is StatusCode.ERROR
    return ReadableSpan(
        name=span.name,
        context=span.context,
        parent=span.parent,
        resource=span.resource,
        attributes=span.attributes,
        events=tuple(_redacted_exception_event(event) for event in span.events),
        links=span.links,
        kind=span.kind,
        status=Status(StatusCode.ERROR, REDACTED_EXCEPTION_DETAIL) if failed else span.status,
        start_time=span.start_time,
        end_time=span.end_time,
        instrumentation_scope=span.instrumentation_scope,
    )


def _redacted_exception_event(event: SdkEvent) -> SdkEvent:
    if event.name != _EXCEPTION_EVENT_NAME:
        return event
    attributes = event.attributes or {}
    kept = {name: attributes[name] for name in _EXCEPTION_ATTRIBUTES_KEPT if name in attributes}
    return SdkEvent(
        event.name,
        {
            **kept,
            "exception.message": REDACTED_EXCEPTION_DETAIL,
            "exception.stacktrace": REDACTED_EXCEPTION_DETAIL,
        },
        event.timestamp,
    )


def _main_event_starter(public_origin: str) -> Callable[[Span, dict[str, Any]], None]:
    """Hook do span da requisição; a URL dele sai de `public_origin`, nunca do header `Host`."""
    server_name = urlsplit(public_origin).netloc

    def start_main_event(span: Span, scope: dict[str, Any]) -> None:
        # Convenção "main = true": o span da requisição é o evento amplo dela. O resto dele vem
        # do middleware do evento amplo, no fim da requisição.
        if not span.is_recording():
            return
        span.set_attribute("main", True)
        _redact_request_attributes(span, scope, public_origin, server_name)

    return start_main_event


def _redact_request_attributes(
    span: Span, scope: dict[str, Any], public_origin: str, server_name: str
) -> None:
    """Tira o caminho, a query string, o IP completo e os headers que a instrumentação ASGI pôs.

    O caminho cru carrega ids e qualquer texto de uma URL sem rota; no lugar dele vai o template
    da rota (ou `UNMATCHED_ROUTE`), e a URL vira `public_origin` + template (sem rota, só a
    origem: `UNMATCHED_ROUTE` colado nela pareceria outro host). A query carrega
    filtros e cursores, que são dados do usuário; User-Agent e Host são texto livre do cliente,
    e nenhum header entra no evento (3.7). Só sobrescreve os atributos que já existem, para não
    inventar nomes de outra versão das convenções.
    """
    present = getattr(span, "attributes", None) or {}
    route = present.get(_ROUTE_ATTRIBUTE)
    client = scope.get("client")
    address = truncate_client_address(str(client[0])) if client else UNKNOWN_ADDRESS
    for names, value in (
        (_URL_ATTRIBUTES, f"{public_origin}{route}" if route else public_origin),
        (_TARGET_ATTRIBUTES, str(route) if route else UNMATCHED_ROUTE),
        (_SERVER_NAME_ATTRIBUTES, server_name),
        (_QUERY_ATTRIBUTES, ""),
        (_CLIENT_ADDRESS_ATTRIBUTES, address),
        (_USER_AGENT_ATTRIBUTES, ""),
    ):
        for name in names:
            if name in present:
                span.set_attribute(name, value)


@dataclass(frozen=True)
class SpanQueueLimits:
    """Limites do `BatchSpanProcessor`; `None` deixa o padrão do SDK (fila 2048, lote 512)."""

    max_queue_size: int | None = None
    max_export_batch_size: int | None = None


SDK_DEFAULT_SPAN_QUEUE = SpanQueueLimits()
# Só a API exporta um span por consulta ao banco. O lote maior é o que escoa um Collector lento;
# a fila segura 30 s dele parado a 20 req/s e custa cerca de 45 MiB cheia. Fica no código porque
# a produção recebe novas imagens, não novas variáveis do Compose.
API_SPAN_QUEUE = SpanQueueLimits(max_queue_size=8192, max_export_batch_size=2048)


def make_span_processor(
    exporter: SpanExporter | None, queue: SpanQueueLimits = SDK_DEFAULT_SPAN_QUEUE
) -> SpanProcessor | None:
    """Processador que exporta os spans; sem `exporter` e sem endpoint OTLP, nenhum.

    `exporter` serve aos testes, que leem os spans de um `InMemorySpanExporter`.
    """
    inner: SpanProcessor
    if exporter is not None:
        inner = SimpleSpanProcessor(exporter)
    elif os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        inner = BatchSpanProcessor(
            OTLPSpanExporter(),
            max_queue_size=queue.max_queue_size,
            max_export_batch_size=queue.max_export_batch_size,
        )
    else:
        return None
    return ExceptionDetailRedactingProcessor(inner)


class ExceptionDetailRedactingProcessor(SpanProcessor):
    """Tira o detalhe das exceções dos spans do SQLAlchemy e dos `main` antes de exportar.

    A instrumentação grava `str(exceção)` no status, e o PostgreSQL põe valores na mensagem:
    `DETAIL: Key (email)=(...)`, `invalid input syntax for type uuid: "..."`. O span `connect`
    também ganha um evento `exception`, cuja mensagem e pilha trazem host, porta e usuário. O
    código do erro da requisição já está no evento amplo (`error.code`), a consulta, parametrizada,
    em `db.statement`, e o tipo da exceção fica no evento. Nos spans `main`, ver
    `_without_main_exception_detail`. O span terminado é imutável, então o processador repassa uma
    cópia.
    """

    def __init__(self, inner: SpanProcessor) -> None:
        self._inner = inner

    def on_start(self, span: SdkSpan, parent_context: Context | None = None) -> None:
        self._inner.on_start(span, parent_context)

    def on_end(self, span: ReadableSpan) -> None:
        self._inner.on_end(_without_main_exception_detail(_without_database_error_detail(span)))

    def shutdown(self) -> None:
        self._inner.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return self._inner.force_flush(timeout_millis)


def _without_database_error_detail(span: ReadableSpan) -> ReadableSpan:
    scope = span.instrumentation_scope
    if scope is None or scope.name != _SQLALCHEMY_SCOPE:
        return span
    failed = span.status.status_code is StatusCode.ERROR
    if not failed and not any(event.name == _EXCEPTION_EVENT for event in span.events):
        return span
    return ReadableSpan(
        name=span.name,
        context=span.context,
        parent=span.parent,
        resource=span.resource,
        attributes=span.attributes,
        events=tuple(_without_exception_detail(event) for event in span.events),
        links=span.links,
        kind=span.kind,
        status=Status(StatusCode.ERROR, REDACTED_DATABASE_ERROR) if failed else span.status,
        start_time=span.start_time,
        end_time=span.end_time,
        instrumentation_scope=scope,
    )


def _without_exception_detail(event: SdkEvent) -> SdkEvent:
    if event.name != _EXCEPTION_EVENT or event.attributes is None:
        return event
    redacted = {name: REDACTED_DATABASE_ERROR for name in _EXCEPTION_DETAIL_ATTRIBUTES}
    attributes = {name: redacted.get(name, value) for name, value in event.attributes.items()}
    return SdkEvent(event.name, attributes, event.timestamp)


# Cabe na carência de cada processo entre o SIGTERM e o SIGKILL da plataforma, somado ao resto da
# parada. API: a margem do lifespan depois do `--timeout-graceful-shutdown` do uvicorn
# (docker/app.Dockerfile), em tests/test_api_server_limits.py, conferida por
# tests/api/test_api_tracer_shutdown.py. Worker e manutenção: o passo em curso mais este prazo,
# contra o `stop_grace_period` de cada serviço, em tests/runs/test_process_tracer_shutdown.py.
TRACING_SHUTDOWN_TIMEOUT_S = 1.5


class _IgnoreIncomingContext(TextMapPropagator):
    """Propagador que não lê nem escreve contexto: cada requisição começa um trace novo.

    `CompositePropagator([])` (o que `OTEL_PROPAGATORS=none` monta) devolve `None` no `extract`
    e derruba a instrumentação ASGI; este devolve o contexto vazio.
    """

    def extract(
        self,
        carrier: CarrierT,
        context: Context | None = None,
        getter: Getter[CarrierT] = default_getter,
    ) -> Context:
        return context if context is not None else Context()

    def inject(
        self,
        carrier: CarrierT,
        context: Context | None = None,
        setter: Setter[CarrierT] = default_setter,
    ) -> None:
        return None

    @property
    def fields(self) -> set[str]:
        return set()


@dataclass(frozen=True)
class ApiTracing:
    """Tracer dos eventos que a API emite fora da requisição e o provider que a parada encerra."""

    tracer: Tracer
    provider: TracerProvider | None = None

    def shut_down(self) -> None:
        """Ver `shut_down_tracer_provider`."""
        shut_down_tracer_provider(self.provider)


def shut_down_tracer_provider(provider: TracerProvider | None) -> None:
    """Exporta o lote pendente em até `TRACING_SHUTDOWN_TIMEOUT_S`; nunca lança.

    É o último passo da parada: o processador descarta todo span emitido depois dele.
    `TracerProvider.shutdown()` não aceita prazo e espera até 30 s por um Collector que não
    responde. Numa thread daemon, a espera para no prazo e o processo sai mesmo assim. O `atexit`
    do SDK, se ainda registrado, encontra o processador já encerrado e volta na hora.
    """
    if provider is None:
        return
    worker = threading.Thread(
        target=_shut_down_provider, args=(provider,), name="tracing-shutdown", daemon=True
    )
    worker.start()
    worker.join(TRACING_SHUTDOWN_TIMEOUT_S)
    if worker.is_alive():
        logger.warning("encerramento do tracing passou do prazo: spans da fila podem se perder")


def _shut_down_provider(provider: TracerProvider) -> None:
    try:
        provider.shutdown()
    except Exception:
        # Na parada, uma falha do exportador não pode esconder outro erro do encerramento.
        logger.exception("encerramento do tracing falhou: spans da fila podem se perder")


def configure_tracing(
    app: FastAPI, engine: Engine, settings: Settings, exporter: SpanExporter | None = None
) -> ApiTracing:
    """Liga os traces da API e devolve o tracer dos eventos fora da requisição e o encerramento.

    Sem `exporter` e sem endpoint OTLP, não instrumenta nada e devolve um tracer que não grava.
    `exporter` serve aos testes, que leem os spans de um `InMemorySpanExporter`.
    """
    # A API não aceita contexto de trace de fora: com o propagador global vazio, a instrumentação
    # ignora `traceparent`, `tracestate` e `baggage` e cada requisição abre um trace novo. O
    # Nginx já apaga esses cabeçalhos (proxy-api.conf); isto cobre quem chega sem passar por ele.
    # Quem precisa do `traceparent` (aceitação, worker) usa um propagador explícito.
    set_global_textmap(_IgnoreIncomingContext())
    provider = make_tracer_provider(settings, "goblinshift-api", exporter, API_SPAN_QUEUE)
    if provider is None:
        return ApiTracing(NoOpTracer())
    FastAPIInstrumentor.instrument_app(
        app,
        tracer_provider=provider,
        excluded_urls="livez,readyz",
        server_request_hook=_main_event_starter(settings.checked_public_origin()),
        # Um span por pedaço enviado multiplicaria o volume num download por streaming; bytes e
        # tempos do envio já estão no evento amplo.
        exclude_spans=["receive", "send"],
    )
    _instrument_database(engine, provider)
    return ApiTracing(provider.get_tracer(__name__), provider)


def _instrument_database(engine: Engine, provider: TracerProvider) -> None:
    """Liga os spans de banco; uma falha aqui custa só esses spans, nunca o boot da API."""
    try:
        # `skip_dep_check`: a 0.66b2, última publicada, ainda recusa o SQLAlchemy 2.1, embora o
        # código dela funcione nele (tests/test_telemetry.py). O suporte oficial já está no main
        # do projeto (open-telemetry/opentelemetry-python-contrib#5120); saiu a versão, sai este
        # parâmetro.
        SQLAlchemyInstrumentor().instrument(
            engine=engine, tracer_provider=provider, skip_dep_check=True
        )
    except Exception:
        logger.exception("instrumentação do banco falhou: a API segue sem spans de banco")
        _clean_up_failed_instrumentation()


def _clean_up_failed_instrumentation() -> None:
    try:
        _remove_partial_database_instrumentation()
    except Exception:
        logger.exception("limpeza da instrumentação do banco falhou: podem sobrar spans de banco")


def _remove_partial_database_instrumentation() -> None:
    # `instrument()` troca as funções e registra os listeners um a um e, ao falhar no meio, não
    # marca nada como instrumentado: `uninstrument()` não desfaria o que já foi feito.
    for module in (sqlalchemy, sqlalchemy.engine, sqlalchemy.engine.create):
        unwrap(module, "create_engine")
    unwrap(Engine, "connect")
    # Fora de `sys.modules` quando foi o próprio import dele que falhou.
    asyncio_module = sys.modules.get("sqlalchemy.ext.asyncio")
    if asyncio_module is not None:
        unwrap(asyncio_module, "create_async_engine")
    EngineTracer.remove_all_event_listeners()  # type: ignore[no-untyped-call]  # lib sem tipos


def make_tracer_provider(
    settings: Settings,
    default_service: str,
    exporter: SpanExporter | None = None,
    queue: SpanQueueLimits = SDK_DEFAULT_SPAN_QUEUE,
) -> TracerProvider | None:
    """Provider com o resource do serviço; `None` sem `exporter` e sem endpoint OTLP.

    Quem cria o provider chama `shutdown()` ao sair, para exportar o lote pendente.
    """
    processor = make_span_processor(exporter, queue)
    if processor is None:
        return None
    resource = Resource.create(
        {
            "service.name": os.environ.get("OTEL_SERVICE_NAME", default_service),
            "service.version": settings.service_version,
            "deployment.environment": settings.environment,
        }
    )
    # `ALWAYS_ON`, não o `ParentBased` padrão: um pai remoto sem amostragem não descarta o span
    # do evento amplo nem os contadores dos alertas. Amostrar é decisão do servidor, nunca de quem
    # manda o `traceparent`.
    provider = TracerProvider(resource=resource, sampler=ALWAYS_ON)
    provider.add_span_processor(processor)
    return provider
