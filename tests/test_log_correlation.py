"""Toda linha de log leva o `request_id` da requisição e o trace e o span correntes (#442).

O usuário reporta o `#ref=<request_id>` da tela de login ou o `X-Request-ID` de uma falha; sem o
mesmo id na linha, o operador não acha o caso no log. O `trace_id` e o `span_id` levam da linha
ao trace no OpenObserve.
"""

import io
import json
import logging
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.trace import format_span_id, format_trace_id

from goblinshift.entrypoints.api import problems
from goblinshift.entrypoints.api.problems import REQUEST_ID_HEADER, RequestContextMiddleware
from goblinshift.platform.telemetry import request_id_bound
from tests.production_log import production_handler

type ProductionLog = tuple[logging.Logger, io.StringIO]

CORRELATION_FIELDS = {"request_id", "trace_id", "span_id"}


@pytest.fixture
def production_log_root() -> Iterator[io.StringIO]:
    """O handler de produção no root, onde caem os logs das rotas e do middleware."""
    stream = io.StringIO()
    handler = production_handler(stream)
    root = logging.getLogger()
    root.addHandler(handler)
    yield stream
    root.removeHandler(handler)


def _lines(stream: io.StringIO) -> list[dict[str, Any]]:
    return [json.loads(line) for line in stream.getvalue().splitlines()]


def _log(log: ProductionLog, **fields: object) -> dict[str, Any]:
    logger, stream = log
    logger.warning("evento", extra={"fields": fields})
    return _lines(stream)[-1]


def _app_logging_on_route() -> FastAPI:
    app = FastAPI()
    app.add_middleware(RequestContextMiddleware)

    @app.get("/x")
    def x() -> None:
        logging.getLogger("tests.rota").warning("recusado")

    return app


def test_linha_dentro_de_um_span_traz_trace_e_span(production_log: ProductionLog) -> None:
    with TracerProvider().get_tracer("t").start_as_current_span("s") as span:
        line = _log(production_log)

    context = span.get_span_context()
    assert line["trace_id"] == format_trace_id(context.trace_id)
    assert line["span_id"] == format_span_id(context.span_id)
    assert len(line["trace_id"]) == 32
    assert len(line["span_id"]) == 16


def test_linha_fora_de_span_e_de_requisicao_nao_traz_ids(production_log: ProductionLog) -> None:
    assert CORRELATION_FIELDS.isdisjoint(_log(production_log))


def test_linha_dentro_do_request_id_traz_o_id(production_log: ProductionLog) -> None:
    with request_id_bound("req-1"):
        line = _log(production_log)

    assert line["request_id"] == "req-1"
    assert {"trace_id", "span_id"}.isdisjoint(line)


def test_request_id_bound_restaura_o_anterior_ao_sair(production_log: ProductionLog) -> None:
    with request_id_bound("externo"):
        with request_id_bound("interno"):
            inner = _log(production_log)
        outer = _log(production_log)
    after = _log(production_log)

    assert inner["request_id"] == "interno"
    assert outer["request_id"] == "externo"
    assert "request_id" not in after


def test_request_id_bound_restaura_mesmo_com_excecao(production_log: ProductionLog) -> None:
    with pytest.raises(RuntimeError), request_id_bound("falhou"):
        raise RuntimeError

    assert "request_id" not in _log(production_log)


def test_campo_de_evento_nao_forja_os_ids_da_linha(
    production_log: ProductionLog, canary: str
) -> None:
    with request_id_bound("verdadeiro"):
        line = _log(production_log, request_id=canary, trace_id=canary, span_id=canary)

    assert line["request_id"] == "verdadeiro"
    assert {"trace_id", "span_id"}.isdisjoint(line)
    assert line["dropped_fields"] == 3
    assert canary not in json.dumps(line)


def test_extra_de_quem_registra_nao_forja_os_ids_da_linha(
    production_log: ProductionLog, canary: str
) -> None:
    logger, stream = production_log

    logger.warning("evento", extra={"request_id": canary, "trace_id": canary, "span_id": canary})

    line = _lines(stream)[-1]
    assert CORRELATION_FIELDS.isdisjoint(line)
    assert canary not in stream.getvalue()


def test_log_da_rota_traz_o_mesmo_request_id_da_resposta(production_log_root: io.StringIO) -> None:
    response = TestClient(_app_logging_on_route()).get("/x")

    [line] = [line for line in _lines(production_log_root) if line["logger"] == "tests.rota"]
    assert line["request_id"] == response.headers[REQUEST_ID_HEADER]


def test_log_do_500_traz_o_request_id(
    production_log_root: io.StringIO, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Um `dictConfig` de outro teste pode ter desligado o logger do módulo.
    monkeypatch.setattr(problems.logger, "disabled", False)
    app = FastAPI()
    app.add_middleware(RequestContextMiddleware)

    @app.get("/x")
    def x() -> None:
        raise RuntimeError

    response = TestClient(app, raise_server_exceptions=False).get("/x")

    assert response.status_code == 500
    [line] = [line for line in _lines(production_log_root) if line["logger"] == problems.__name__]
    assert line["level"] == "ERROR"
    assert line["request_id"] == response.headers[REQUEST_ID_HEADER]


def test_request_id_nao_vaza_para_a_requisicao_seguinte(production_log_root: io.StringIO) -> None:
    client = TestClient(_app_logging_on_route())

    first = client.get("/x")
    second = client.get("/x")
    logging.getLogger("tests.rota").warning("fora da requisição")

    lines = [line for line in _lines(production_log_root) if line["logger"] == "tests.rota"]
    assert [line.get("request_id") for line in lines] == [
        first.headers[REQUEST_ID_HEADER],
        second.headers[REQUEST_ID_HEADER],
        None,
    ]
    assert first.headers[REQUEST_ID_HEADER] != second.headers[REQUEST_ID_HEADER]
