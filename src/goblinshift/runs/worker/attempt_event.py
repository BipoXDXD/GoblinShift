"""Peças do span `attempt`, o evento amplo de cada tentativa (docs/GoblinShiftPlan.md, 3.7).

O span liga-se ao trace da requisição de aceitação por *link*, não como filho: a espera na fila
pode durar horas. Os tempos de `inspect_ms`, `process_ms` e `package_ms` vêm do executor, sem
rede, no `telemetry.json` da área de trabalho; o resto o coordenador mede. O contrato dos
atributos está no runbook `deploy/openobserve/README.md`.

O `telemetry.json` é escrito pelo sandbox: lido com teto de bytes, sem seguir symlink e só se for
arquivo regular. Qualquer desvio do schema descarta o documento inteiro, porque telemetria nunca
derruba a tentativa.
"""

import os
import stat
from pathlib import Path

from opentelemetry.trace import Link, get_current_span
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from opentelemetry.util.types import AttributeValue

from goblinshift.runs.worker.strict_json import parse_strict_json

SPAN_NAME = "attempt"
TIMINGS_FILE = "telemetry.json"
# O documento válido tem menos de 100 bytes; o teto só protege o worker de um arquivo hostil.
TIMINGS_MAX_BYTES = 4096
TIMINGS_SCHEMA_VERSION = 1
EXECUTOR_PHASES = frozenset({"inspect_ms", "process_ms", "package_ms"})
_SCHEMA_KEY = "schema_version"
_PROPAGATOR = TraceContextTextMapPropagator()


def links_to_acceptance(traceparent: str | None) -> list[Link]:
    """O link para o span da aceitação; nenhum se o `traceparent` falta ou é inválido."""
    if not traceparent:
        return []
    context = _PROPAGATOR.extract({"traceparent": traceparent})
    span_context = get_current_span(context).get_span_context()
    if not span_context.is_valid:
        return []
    return [Link(span_context)]


def read_executor_timings(path: Path) -> dict[str, int]:
    """Tempos das fases do executor, em ms; vazio se o arquivo falta ou foge do schema."""
    data = _read_regular_file(path, TIMINGS_MAX_BYTES)
    if data is None:
        return {}
    try:
        document = parse_strict_json(data)
    except ValueError:
        return {}
    if not isinstance(document, dict) or document.get(_SCHEMA_KEY) != TIMINGS_SCHEMA_VERSION:
        return {}
    timings = {key: value for key, value in document.items() if key != _SCHEMA_KEY}
    if not timings.keys() <= EXECUTOR_PHASES or not all(map(_is_duration, timings.values())):
        return {}
    return timings


def record(attributes: dict[str, AttributeValue]) -> None:
    """Acrescenta atributos ao span da tentativa em curso; sem span gravando, não faz nada."""
    get_current_span().set_attributes(attributes)


def _is_duration(value: object) -> bool:
    # `bool` é subclasse de `int` em Python: `true` não é duração.
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _read_regular_file(path: Path, max_bytes: int) -> bytes | None:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        # Ausente, symlink (ELOOP) ou ilegível.
        return None
    with os.fdopen(fd, "rb") as source:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        data = source.read(max_bytes + 1)
    return data if len(data) <= max_bytes else None
