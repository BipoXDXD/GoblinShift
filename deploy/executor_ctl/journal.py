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

    A mensagem pode trazer o que o cliente do socket mandou; o local da falha vem dos frames. Cada
    exceção ligada leva um rótulo fixo que diz a relação: causa, contexto ou membro de grupo.
    """
    return "\n\n".join(
        (f"[{label}]\n" if label else "")
        + "Traceback (most recent call last):\n"
        + "".join(traceback.format_tb(item.__traceback__))
        + f"{type(item).__module__}.{type(item).__qualname__}"
        for label, item in _exception_chain(exception)
    )


def _exception_chain(exception: BaseException) -> Iterator[tuple[str | None, BaseException]]:
    """A exceção e as que o traceback mostraria, cada uma com a relação com a anterior."""
    pending: list[tuple[str | None, BaseException]] = [(None, exception)]
    seen: set[int] = set()
    while pending:
        label, current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        yield label, current
        if current.__cause__ is not None:
            pending.append(("cause", current.__cause__))
        elif current.__context__ is not None and not current.__suppress_context__:
            pending.append(("context", current.__context__))
        if isinstance(current, BaseExceptionGroup):
            pending.extend(("group member", member) for member in current.exceptions)


def configure_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)
