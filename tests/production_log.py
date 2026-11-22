"""O handler de produção dos logs, para testes que leem a saída inteira dele.

`production_handler` monta o que `configure_logging` põe no root (o `JsonFormatter` com os filtros
`omit_exception_detail` e `add_correlation_ids`), escrevendo num buffer. As fixtures são
registradas em `tests/conftest.py`.
"""

import io
import logging
import uuid
from collections.abc import Callable, Iterator

import pytest

from goblinshift.platform.telemetry import (
    JsonFormatter,
    add_correlation_ids,
    omit_exception_detail,
)


def production_handler(stream: io.StringIO) -> logging.Handler:
    """O handler que `configure_logging` põe no root, escrevendo num buffer."""
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(omit_exception_detail)
    handler.addFilter(add_correlation_ids)
    return handler


@pytest.fixture
def canary() -> str:
    return f"CANARY-{uuid.uuid4()}"


@pytest.fixture
def production_log() -> Iterator[tuple[logging.Logger, io.StringIO]]:
    stream = io.StringIO()
    handler = production_handler(stream)
    logger = logging.getLogger(f"tests.production_log.{uuid.uuid4()}")
    logger.propagate = False
    logger.addHandler(handler)
    yield logger, stream
    logger.removeHandler(handler)


@pytest.fixture
def listen(monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[[logging.Logger], io.StringIO]]:
    """Liga o handler de produção ao logger de um módulo e devolve o buffer da saída."""
    attached: list[tuple[logging.Logger, logging.Handler]] = []

    def attach(logger: logging.Logger) -> io.StringIO:
        stream = io.StringIO()
        handler = production_handler(stream)
        monkeypatch.setattr(logger, "disabled", False)
        logger.addHandler(handler)
        attached.append((logger, handler))
        return stream

    yield attach
    for logger, handler in attached:
        logger.removeHandler(handler)
