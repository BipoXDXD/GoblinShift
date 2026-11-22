"""Log estruturado: uma linha JSON por evento no stdout, que o systemd leva ao journal.

Só campos de uma allowlist saem no log; conteúdo de arquivo, corpo de requisição e allowlist de
imagens nunca entram. Uma exceção sai só com tipos e frames. O journal já carimba data e hora.
"""

import json
import logging
import sys
import traceback
from collections.abc import Iterator

LOGGED_FIELDS = ("operation", "attempt_id", "image", "status", "error", "detail", "duration_ms")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, object] = {
            "level": record.levelname.lower(),
            "event": record.getMessage(),
        }
        for field in LOGGED_FIELDS:
            if field in record.__dict__:
                entry[field] = record.__dict__[field]
        exception = record.exc_info[1] if record.exc_info else None
        if exception is not None:
            entry["exception"] = _types_and_frames(exception)
        return json.dumps(entry, ensure_ascii=False)


def _types_and_frames(exception: BaseException) -> str:
    """O traceback sem mensagens, notas nem texto de grupo: só o tipo e os frames de cada exceção.

    A mensagem pode trazer o que o cliente do socket mandou; o local da falha vem dos frames.
    """
    return "\n\n".join(
        "Traceback (most recent call last):\n"
        + "".join(traceback.format_tb(item.__traceback__))
        + f"{type(item).__module__}.{type(item).__qualname__}"
        for item in _exception_chain(exception)
    )


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


def configure_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)
