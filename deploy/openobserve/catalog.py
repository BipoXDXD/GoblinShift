"""SLOs e alertas do GoblinShift como dados (docs/GoblinShiftPlan.md, 3.7).

As consultas ficam em `sql/`, uma por arquivo, com comentários que explicam cada critério; aqui
elas são lidas, sem os comentários, e montadas com `string.Template` (`${stream}`, `${events}`).
O provisionamento (`provision.py`) só envia o que este módulo descreve.
"""

from dataclasses import dataclass
from enum import StrEnum
from functools import cache
from pathlib import Path
from string import Template

SQL_DIR = Path(__file__).resolve().parent / "sql"

# Janela das duas avaliações da queima, em minutos: a longa é o período do alerta.
LONG_WINDOW_MINUTES = 60
SHORT_WINDOW_DIVISOR = LONG_WINDOW_MINUTES // 15
# Avaliação a cada 5 min: a janela curta de 15 min ainda pega a queima em curso.
BURN_FREQUENCY_MINUTES = 5


class DestinationKey(StrEnum):
    """Para onde um alerta notifica; cada um vira um destino do OpenObserve."""

    URGENT = "urgente"
    WARNING = "aviso"
    HEARTBEAT = "heartbeat"


# Queima de 2x o ritmo permitido gera aviso; 4x ou mais, alerta urgente (3.7).
BURN_LEVELS: tuple[tuple[DestinationKey, int], ...] = (
    (DestinationKey.WARNING, 2),
    (DestinationKey.URGENT, 4),
)
# Depois de notificar, quanto tempo o mesmo alerta fica quieto.
BURN_SILENCE_MINUTES = {DestinationKey.WARNING: 180, DestinationKey.URGENT: 60}


@dataclass(frozen=True)
class Slo:
    """Um SLO baseado em eventos: proporção de bons entre os elegíveis (3.7).

    `budget_denominator` é N na meta 1 - 1/N (99,5% → 200), para a queima comparar inteiros.
    `min_events` é o mínimo de elegíveis na janela de 1 h para a queima disparar; a de 15 min
    exige um quarto dele. `columns` são os atributos do span que a consulta lê, já com os nomes
    do OpenObserve: o provisionamento os declara no stream antes de validar as consultas.
    """

    name: str
    budget_denominator: int
    min_events: int
    columns: tuple[str, ...]

    @property
    def target(self) -> float:
        return 1 - 1 / self.budget_denominator

    @property
    def min_events_short(self) -> int:
        return -(-self.min_events // SHORT_WINDOW_DIVISOR)

    def events_sql(self, stream: str) -> str:
        return _render(f"{self.name}.eventos.sql", stream=stream)

    def sli_sql(self, stream: str) -> str:
        return _render("sli.sql", events=self.events_sql(stream))

    def burn_sql(self, stream: str, burn_rate: int) -> str:
        return _render(
            "queima.sql",
            events=self.events_sql(stream),
            min_events_long=str(self.min_events),
            min_events_short=str(self.min_events_short),
            budget_denominator=str(self.budget_denominator),
            burn_rate=str(burn_rate),
        )


_HTTP_COLUMNS = ("main", "http_method", "http_route", "http_status_code")

# Mínimos: 1 ruim em `min_events` precisa ficar abaixo do aviso (2/N), senão uma falha isolada
# dispara (3.7). Com N = 200, mais de 100 eventos; com N = 100, mais de 50.
SLOS: tuple[Slo, ...] = (
    Slo("aceitacao", 200, 101, (*_HTTP_COLUMNS, "error_code", "duration_ms")),
    Slo(
        "execucao",
        100,
        51,
        ("main", "error_code", "run_duration_ms", "slo_execution_eligible"),
    ),
    Slo(
        "download",
        200,
        101,
        (*_HTTP_COLUMNS, "http_response_first_byte_ms", "http_response_completed"),
    ),
)


def slo_named(name: str) -> Slo:
    for slo in SLOS:
        if slo.name == name:
            return slo
    raise KeyError(name)


@dataclass(frozen=True)
class Alert:
    """Um alerta agendado: dispara quando a consulta devolve uma linha ou mais."""

    name: str
    description: str
    stream_type: str
    stream_name: str
    sql: str
    period_minutes: int
    frequency_minutes: int
    silence_minutes: int
    destination: DestinationKey


DISK_STREAM = "system_filesystem_utilization"


def alerts(stream: str, prefix: str) -> list[Alert]:
    """Todos os alertas: queima por SLO e nível, causas conhecidas e o heartbeat."""
    return [
        *(
            _burn_alert(slo, level, rate, stream, prefix)
            for slo in SLOS
            for level, rate in BURN_LEVELS
        ),
        Alert(
            name=f"{prefix}causa-fila-cheia",
            description=(
                "Fila global cheia há mais de 15 min: POST /runs recusado com QUEUE_FULL em cada "
                "fatia de 5 min. Falta capacidade; veja o runbook em deploy/openobserve/README.md."
            ),
            stream_type="traces",
            stream_name=stream,
            sql=_render("causa-fila-cheia.sql", stream=stream),
            period_minutes=15,
            frequency_minutes=5,
            silence_minutes=60,
            destination=DestinationKey.URGENT,
        ),
        Alert(
            name=f"{prefix}causa-disco",
            description=(
                "Filesystem do PostgreSQL, do storage ou da telemetria acima de 85% nos últimos "
                "10 min (a linha traz o ponto de montagem). Veja o runbook."
            ),
            stream_type="metrics",
            stream_name=DISK_STREAM,
            sql=_render("causa-disco.sql"),
            period_minutes=10,
            frequency_minutes=5,
            silence_minutes=240,
            destination=DestinationKey.URGENT,
        ),
        Alert(
            name=f"{prefix}heartbeat",
            description="Heartbeat dos alertas: pinga o healthchecks.io a cada hora.",
            stream_type="metrics",
            stream_name=DISK_STREAM,
            sql=_render("heartbeat.sql"),
            period_minutes=60,
            frequency_minutes=60,
            silence_minutes=0,
            destination=DestinationKey.HEARTBEAT,
        ),
    ]


def _burn_alert(slo: Slo, level: DestinationKey, burn_rate: int, stream: str, prefix: str) -> Alert:
    return Alert(
        name=f"{prefix}slo-{slo.name}-{level.value}",
        description=(
            f"SLO de {slo.name} (meta {slo.target:.1%}) queimando o orçamento a {burn_rate}x o "
            f"ritmo permitido ou mais, na última hora e nos últimos 15 min. Veja o runbook em "
            f"deploy/openobserve/README.md."
        ),
        stream_type="traces",
        stream_name=stream,
        sql=slo.burn_sql(stream, burn_rate),
        period_minutes=LONG_WINDOW_MINUTES,
        frequency_minutes=BURN_FREQUENCY_MINUTES,
        silence_minutes=BURN_SILENCE_MINUTES[level],
        destination=level,
    )


def _render(file_name: str, **values: str) -> str:
    """Consulta do arquivo, sem comentários, com os marcadores substituídos.

    `substitute` falha com um marcador sem valor, em vez de mandar `${x}` ao OpenObserve.
    """
    return Template(_without_comments(file_name)).substitute(values)


@cache
def _without_comments(file_name: str) -> str:
    lines = (SQL_DIR / file_name).read_text(encoding="utf-8").splitlines()
    return "\n".join(line for line in lines if not line.lstrip().startswith("--")).strip()
