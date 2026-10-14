"""Log estruturado: uma linha JSON por evento no stdout, que o systemd leva ao journal.

Só campos de uma allowlist saem no log; conteúdo de arquivo, corpo de requisição e allowlist de
imagens nunca entram. O journal já carimba data e hora.
"""

import json
import logging
import sys

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
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False)


def configure_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)
