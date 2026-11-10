"""Logs JSON e traces OpenTelemetry (docs/GoblinShiftPlan.md, 3.7).

Os traces só são exportados quando `OTEL_EXPORTER_OTLP_ENDPOINT` está definido; o
endpoint, o nome do serviço e os headers seguem as variáveis padrão do OpenTelemetry.
"""

import hashlib
import hmac
import ipaddress
import json
import logging
import os
import re
import sys
import threading
import traceback
from collections.abc import Callable, Iterator
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
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import Event as SdkEvent
from opentelemetry.sdk.trace import ReadableSpan, SpanProcessor, TracerProvider
from opentelemetry.sdk.trace import Span as SdkSpan
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor, SpanExporter
from opentelemetry.trace import NoOpTracer, Span, Status, StatusCode, Tracer, get_current_span
from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError

from goblinshift.platform.settings import Settings

ALERT_ATTRIBUTE_PREFIX = "alert."
UVICORN_ERROR_LOGGER = "uvicorn.error"
_DATABASE_ERRORS = (SQLAlchemyError, psycopg.Error)
_POOL_LOGGER_PREFIX = "sqlalchemy.pool"
# O repr da conexão traz host, porta, usuário e banco, e o pool o põe no texto das mensagens.
_PSYCOPG_CONNECTION_REPR = re.compile(r"<psycopg\.\w*Connection [^>]*>")
# Classe (2) e condição (3) do PostgreSQL; o que não casar não vai para o log nem para a tela.
_SQLSTATE = re.compile(r"[0-9A-Z]{5}")
_SQLSTATE_ATTRIBUTE = "sqlstate"

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
        if isinstance(fields, dict):
            entry.update(fields)
        sqlstate = getattr(record, _SQLSTATE_ATTRIBUTE, None)
        if sqlstate is not None:
            entry[_SQLSTATE_ATTRIBUTE] = sqlstate
        if record.exc_info:
            entry["exception"] = record.exc_text or self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False)


def omit_database_error_detail(record: logging.LogRecord) -> bool:
    """Filtro de log: exceção com erro de banco na cadeia sai só com tipos e frames.

    A mensagem do SQLAlchemy e do psycopg traz host, SQL e o `DETAIL` do PostgreSQL, com valores
    do usuário (3.7). O texto vai em `exc_text`, que os formatadores usam no lugar de formatar
    `exc_info`: o registro continua com a exceção. O SQLSTATE do PostgreSQL, que é só um código,
    fica junto do tipo e no atributo `sqlstate` do registro. Qualquer outra exceção sai inteira. As
    mensagens do pool perdem o repr da conexão.
    """
    if record.name.startswith(_POOL_LOGGER_PREFIX):
        record.msg = _PSYCOPG_CONNECTION_REPR.sub("<psycopg connection>", record.getMessage())
        record.args = None
    exception = record.exc_info[1] if record.exc_info else None
    if exception is None:
        return True
    if is_database_error(exception):
        chain = _exception_chain(exception)
        record.exc_text = "\n\n".join(_type_and_frames(item) for item in chain)
        sqlstate = database_sqlstate(exception)
        if sqlstate is not None:
            setattr(record, _SQLSTATE_ATTRIBUTE, sqlstate)
    return True


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


def _type_and_frames(exception: BaseException) -> str:
    frames = "".join(traceback.format_tb(exception.__traceback__))
    kind = type(exception)
    described = f"{kind.__module__}.{kind.__qualname__}"
    if isinstance(exception, psycopg.Error):
        sqlstate = _well_formed_sqlstate(exception)
        if sqlstate is not None:
            described = f"{described} (SQLSTATE {sqlstate})"
    return f"Traceback (most recent call last):\n{frames}{described}"


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
    handler.addFilter(omit_database_error_detail)
    logging.basicConfig(level=logging.INFO, handlers=[handler, AlertEventCounter()], force=True)
    # O Uvicorn escreve por handlers próprios, fora do root (`propagate: False`).
    logging.getLogger(UVICORN_ERROR_LOGGER).addFilter(omit_database_error_detail)


# Prefixos que sobram do IP do cliente no evento: o bastante para ver rede ou provedor, nunca a
# pessoa (3.7: "nunca entram em evento: ... IP completo").
IPV4_PREFIX_BITS = 24
IPV6_PREFIX_BITS = 48
UNKNOWN_ADDRESS = "unknown"
# Atributos que a instrumentação ASGI preenche com a URL completa, query string incluída, ou com
# o IP do cliente; nomes antigos e novos das convenções semânticas.
_URL_ATTRIBUTES = ("http.url", "url.full")
_TARGET_ATTRIBUTES = ("http.target",)
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
    """Tira a query string, o IP completo e os headers que a instrumentação ASGI pôs no span.

    A query carrega filtros e cursores, que são dados do usuário; User-Agent e Host são texto
    livre do cliente, e nenhum header entra no evento (3.7). Só sobrescreve os atributos que já
    existem, para não inventar nomes de outra versão das convenções.
    """
    present = getattr(span, "attributes", None) or {}
    path = str(scope.get("path", ""))
    client = scope.get("client")
    address = truncate_client_address(str(client[0])) if client else UNKNOWN_ADDRESS
    for names, value in (
        (_URL_ATTRIBUTES, f"{public_origin}{path}"),
        (_TARGET_ATTRIBUTES, path),
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


# Cabe na margem que sobra ao lifespan entre o fim do `--timeout-graceful-shutdown` do uvicorn
# (docker/app.Dockerfile) e o SIGKILL da plataforma. A margem está em
# tests/test_api_server_limits.py, e tests/api/test_api_tracer_shutdown.py confere a parada nela.
TRACING_SHUTDOWN_TIMEOUT_S = 1.5


@dataclass(frozen=True)
class ApiTracing:
    """Tracer dos eventos que a API emite fora da requisição e o provider que a parada encerra."""

    tracer: Tracer
    provider: TracerProvider | None = None

    def shut_down(self) -> None:
        """Exporta o lote pendente em até `TRACING_SHUTDOWN_TIMEOUT_S`; nunca lança.

        `TracerProvider.shutdown()` não aceita prazo e espera até 30 s por um Collector que não
        responde. Numa thread daemon, a espera para no prazo e o processo sai mesmo assim. O
        `atexit` do SDK, se ainda registrado, encontra o processador já encerrado e volta na hora.
        """
        if self.provider is None:
            return
        worker = threading.Thread(
            target=_shut_down_provider, args=(self.provider,), name="tracing-shutdown", daemon=True
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
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(processor)
    return provider
