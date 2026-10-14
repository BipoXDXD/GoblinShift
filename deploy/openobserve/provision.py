"""Provisionamento idempotente dos alertas no OpenObserve (docs/GoblinShiftPlan.md, 3.7).

Converge o OpenObserve para o catálogo: cria ou atualiza o template, os destinos e os alertas
com o prefixo dado, e remove os alertas com esse prefixo que saíram do catálogo. O que não tem o
prefixo não é tocado. Rodar duas vezes seguidas não muda nada na segunda.

Antes de mexer em alertas e destinos, cada consulta é validada contra o OpenObserve. Para isso,
num stream de traces que ainda não tem todas as colunas dos SLOs (o evento de término da Run,
por exemplo, só existe quando o worker o emite), um span-semente com `main = false` as declara:
sem isso, a consulta falharia em toda avaliação. A semente não entra em nenhum SLO, porque todas
as consultas filtram `main = 'true'`.
"""

import os
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from openobserve.catalog import SLOS, Alert, DestinationKey
from openobserve.client import OpenObserve, OpenObserveError

SEED_SPAN_NAME = "goblinshift.schema-seed"
SEED_SERVICE = "goblinshift-slo-provision"
SCHEMA_TIMEOUT_S = 30.0
SCHEMA_POLL_S = 0.5
US_PER_MINUTE = 60_000_000
# Corpo do webhook: "text" serve ao Slack, ao Telegram (sendMessage) e ao ntfy; "content", ao
# Discord. As descrições do catálogo não têm aspas nem quebras de linha, então o JSON fica válido.
TEMPLATE_BODY = (
    '{"text": "[GoblinShift] {alert_name}: {alert_description}", '
    '"content": "[GoblinShift] {alert_name}: {alert_description}", '
    '"alert": "{alert_name}"}'
)
TRACE_COLUMNS = tuple(sorted({column for slo in SLOS for column in slo.columns}))


class ProvisionError(Exception):
    """O provisionamento parou antes de mudar alertas e destinos."""


def provision(
    api: OpenObserve,
    *,
    alerts: Sequence[Alert],
    destinations: Mapping[DestinationKey, str],
    prefix: str,
    log: Callable[[str], None] = lambda _line: None,
) -> None:
    """Converge os alertas com `prefix` para `alerts`; `destinations` dá a URL de cada destino."""
    if alerts:
        _check_destinations(alerts, destinations)
        for stream in sorted({a.stream_name for a in alerts if a.stream_type == "traces"}):
            declare_columns(api, stream, log)
        _validate_queries(api, alerts)
        template = f"{prefix}notificacao"
        _log_change(log, "template", template, api.save_template(template, TEMPLATE_BODY))
        for key in sorted({alert.destination for alert in alerts}):
            name = f"{prefix}{key.value}"
            _log_change(
                log, "destino", name, _save_destination(api, name, destinations[key], template)
            )
    _converge_alerts(api, alerts, prefix, log)


def _save_destination(api: OpenObserve, name: str, url: str, template: str) -> bool:
    """Grava o destino sem repassar a resposta do OpenObserve, que pode repetir a URL (segredo)."""
    try:
        return api.save_destination(name, url, template)
    except OpenObserveError:
        raise ProvisionError(
            f"o OpenObserve recusou o destino {name}; confira se a URL é HTTPS e pública "
            "(resposta omitida porque pode conter a URL)"
        ) from None


def _check_destinations(
    alerts: Sequence[Alert], destinations: Mapping[DestinationKey, str]
) -> None:
    missing = sorted({a.destination.value for a in alerts} - {k.value for k in destinations})
    if missing:
        raise ProvisionError(f"faltam URLs para os destinos: {', '.join(missing)}")


def declare_columns(
    api: OpenObserve, stream: str, log: Callable[[str], None] = lambda _line: None
) -> None:
    """Garante no stream de traces todas as colunas que as consultas dos SLOs leem."""
    missing = set(TRACE_COLUMNS) - api.stream_fields(stream, "traces")
    if not missing:
        return
    api.ingest_traces(stream, [_seed_span(missing)])
    deadline = time.monotonic() + SCHEMA_TIMEOUT_S
    while not set(TRACE_COLUMNS) <= api.stream_fields(stream, "traces"):
        if time.monotonic() > deadline:
            raise ProvisionError(f"o stream {stream} não registrou as colunas da semente a tempo")
        time.sleep(SCHEMA_POLL_S)
    log(f"colunas declaradas no stream {stream}: {', '.join(sorted(missing))}")


def _seed_span(columns: set[str]) -> dict[str, Any]:
    now_ns = time.time_ns()
    # "0" serve a todas: as colunas numéricas passam pelo TRY_CAST das consultas.
    attributes = [{"key": "main", "value": {"boolValue": False}}] + [
        {"key": column, "value": {"stringValue": "0"}}
        for column in sorted(columns)
        if column != "main"
    ]
    return {
        "resource": {
            "attributes": [{"key": "service.name", "value": {"stringValue": SEED_SERVICE}}]
        },
        "scopeSpans": [
            {
                "scope": {"name": SEED_SERVICE},
                "spans": [
                    {
                        "traceId": os.urandom(16).hex(),
                        "spanId": os.urandom(8).hex(),
                        "name": SEED_SPAN_NAME,
                        "kind": 1,
                        "startTimeUnixNano": str(now_ns),
                        "endTimeUnixNano": str(now_ns),
                        "attributes": attributes,
                    }
                ],
            }
        ],
    }


def _validate_queries(api: OpenObserve, alerts: Sequence[Alert]) -> None:
    now_us = time.time_ns() // 1000
    failures: list[str] = []
    for alert in alerts:
        try:
            api.search(
                alert.sql,
                alert.stream_type,
                now_us - alert.period_minutes * US_PER_MINUTE,
                now_us,
            )
        except OpenObserveError as error:
            failures.append(f"  {alert.name}: {error}")
    if failures:
        raise ProvisionError(
            "o OpenObserve recusou consultas; nenhum alerta nem destino foi alterado:\n"
            + "\n".join(failures)
        )


def _converge_alerts(
    api: OpenObserve, alerts: Sequence[Alert], prefix: str, log: Callable[[str], None]
) -> None:
    existing = api.alerts_named(prefix)
    for alert in alerts:
        payload = _payload(alert, prefix)
        if alert.name in existing:
            api.update_alert(existing[alert.name], payload)
            _log_change(log, "alerta", alert.name, created=False)
        else:
            api.create_alert(payload)
            _log_change(log, "alerta", alert.name, created=True)
    wanted = {alert.name for alert in alerts}
    for name, alert_id in sorted(existing.items()):
        if name not in wanted:
            api.delete_alert(alert_id)
            log(f"alerta removido: {name}")


def _payload(alert: Alert, prefix: str) -> dict[str, Any]:
    return {
        "name": alert.name,
        "description": alert.description,
        "stream_type": alert.stream_type,
        "stream_name": alert.stream_name,
        "is_real_time": False,
        "enabled": True,
        "query_condition": {"type": "sql", "sql": alert.sql},
        "trigger_condition": {
            "period": alert.period_minutes,
            "operator": ">=",
            "threshold": 1,
            "frequency": alert.frequency_minutes,
            "frequency_type": "minutes",
            "silence": alert.silence_minutes,
        },
        "destinations": [f"{prefix}{alert.destination.value}"],
    }


def _log_change(log: Callable[[str], None], kind: str, name: str, created: bool) -> None:
    log(f"{kind} {'criado' if created else 'atualizado'}: {name}")
