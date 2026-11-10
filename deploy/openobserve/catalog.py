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

# Atributos dos spans que as causas abaixo leem, com os nomes do OpenObserve ("." vira "_"); o
# provisionamento os declara no stream junto com os dos SLOs.
CAUSE_COLUMNS: tuple[str, ...] = (
    "main",
    "maintenance_runs_stuck_stopping",
    "maintenance_purge_limit_reached",
    "maintenance_accounts_deletion_overdue",
    "maintenance_storage_global_reserved_bytes",
    "maintenance_storage_global_max_bytes",
    "maintenance_missing_sweep_skipped",
    "maintenance_missing_governor_tripped",
    "maintenance_objects_marked_missing",
    "maintenance_objects_missing_unreadable",
    "maintenance_purge_files_not_removed",
    "maintenance_candidates_left_on_disk",
    "maintenance_missing_sweep_failed",
    "maintenance_failed_cycles",
    "alert_purge_file_not_removed",
    "alert_capacity_divergence",
    "alert_capacity_over_max",
    "alert_upload_without_reservation",
    "alert_run_without_reservation",
    "alert_executor_start_failed",
)


@dataclass(frozen=True)
class _SpanCause:
    """Causa lida do stream de traces: um arquivo SQL e a política de aviso."""

    name: str
    description: str
    period_minutes: int
    silence_minutes: int
    destination: DestinationKey


# Avaliadas a cada 5 min; a manutenção emite um span a cada 30 s.
_SPAN_CAUSE_FREQUENCY_MINUTES = 5
# Mais longo que a suspensão da manutenção pelas sondas da aceitação, que é esperada (ADR 017).
ABSENCE_PERIOD_MINUTES = 30
SPAN_CAUSES: tuple[_SpanCause, ...] = (
    _SpanCause(
        "causa-execucao-presa",
        "Execução presa em stopping há mais de 10 min: o executor não confirmou a parada e a "
        "Run segura a vaga da tentativa. Veja o runbook em deploy/openobserve/README.md.",
        period_minutes=10,
        silence_minutes=60,
        destination=DestinationKey.URGENT,
    ),
    _SpanCause(
        "causa-limitador-de-exclusoes",
        "Limitador de exclusões da manutenção acionado: havia mais de 500 objetos ou 2 GiB a "
        "remover numa volta. Confira se a exclusão em massa é legítima; veja o runbook.",
        period_minutes=10,
        silence_minutes=180,
        destination=DestinationKey.WARNING,
    ),
    _SpanCause(
        "causa-exclusao-de-conta-atrasada",
        "Exclusão de conta pedida há 20 h ou mais e ainda não concluída; o prazo do DELETE /me é "
        "24 h. Veja o runbook em deploy/openobserve/README.md.",
        period_minutes=10,
        silence_minutes=60,
        destination=DestinationKey.URGENT,
    ),
    _SpanCause(
        "causa-capacidade-global",
        "Armazenamento reservado no ambiente acima de 85% do teto global nos últimos 15 min. "
        "Veja o runbook em deploy/openobserve/README.md.",
        period_minutes=15,
        silence_minutes=240,
        destination=DestinationKey.WARNING,
    ),
    _SpanCause(
        "causa-storage-sem-sentinela",
        "Varredura de objetos ausentes suspensa: falta a sentinela de montagem na raiz do "
        "storage, quase sempre o filesystem desmontado. Veja o runbook.",
        period_minutes=10,
        silence_minutes=180,
        destination=DestinationKey.WARNING,
    ),
    _SpanCause(
        "causa-limitador-de-ausentes",
        "Limitador da varredura de objetos ausentes acionado: arquivos demais sumidos num lote e "
        "nada marcado. Storage errado ou backup antigo restaurado; veja o runbook.",
        period_minutes=10,
        silence_minutes=180,
        destination=DestinationKey.WARNING,
    ),
    _SpanCause(
        "causa-objetos-ausentes",
        "Objetos marcados missing: o arquivo sumiu do storage montado. Esperado depois de "
        "restaurar o banco de um backup; fora disso, investigue. Veja o runbook.",
        period_minutes=10,
        silence_minutes=180,
        destination=DestinationKey.WARNING,
    ),
    _SpanCause(
        "causa-objetos-ilegiveis",
        "Varredura de objetos ausentes com objetos ilegíveis: o disco devolveu erro que não é "
        "ausência (EIO, permissão). O log da manutenção traz errno e chaves; veja o runbook.",
        period_minutes=10,
        silence_minutes=180,
        destination=DestinationKey.WARNING,
    ),
    _SpanCause(
        "causa-arquivos-nao-removidos",
        "Arquivo ficou no disco depois de a exclusão ser confirmada no banco, e nada repete a "
        "remoção. O log do processo traz a chave; veja o runbook.",
        period_minutes=10,
        silence_minutes=180,
        destination=DestinationKey.WARNING,
    ),
    _SpanCause(
        "causa-varredura-falhou",
        "Varredura de objetos ausentes falhando em três voltas ou mais nos últimos 10 min. O "
        "traceback está no log da manutenção; veja o runbook.",
        period_minutes=10,
        silence_minutes=180,
        destination=DestinationKey.WARNING,
    ),
    _SpanCause(
        "causa-ciclos-falhando",
        "Ciclo da manutenção falhando em três voltas ou mais nos últimos 10 min. O log da "
        "manutenção traz o ciclo e o traceback; veja o runbook.",
        period_minutes=10,
        silence_minutes=60,
        destination=DestinationKey.URGENT,
    ),
    _SpanCause(
        "causa-capacidade-divergente",
        "Livro-razão de capacidade divergente dos pools ou acima do teto. A reconciliação corrige "
        "o pool, mas a origem precisa ser achada; veja o runbook.",
        period_minutes=10,
        silence_minutes=180,
        destination=DestinationKey.WARNING,
    ),
    _SpanCause(
        "causa-sem-reserva",
        "Upload ou publicação sem reserva de capacidade: a operação seguiu e a reconciliação adota "
        "o objeto, mas isso só cabe a registros antigos. Veja o runbook.",
        period_minutes=10,
        silence_minutes=180,
        destination=DestinationKey.WARNING,
    ),
    _SpanCause(
        "causa-executor-nao-iniciou",
        "Executor não chegou a rodar em três tentativas ou mais nos últimos 10 min: as Runs "
        "terminam em INFRA_POISON. São duas causas, e o log do worker diz qual. O executor-ctl "
        "recusou o start (log executor não iniciou): leia o motivo no campo error antes de agir; "
        "work_area_dirty também conta, e a imagem no daemon é a primeira suspeita só com "
        "docker_unavailable. Ou a área de trabalho da tentativa não foi criada antes do start (log "
        "área de trabalho falhou): confira a montagem de /srv/goblinshift/exec-work, não a imagem. "
        "Veja o runbook em deploy/openobserve/README.md.",
        period_minutes=10,
        silence_minutes=60,
        destination=DestinationKey.URGENT,
    ),
    _SpanCause(
        "causa-manutencao-sem-ciclo",
        "Nenhuma volta da manutenção concluída em 30 min: processo parado ou travado. Veja o "
        "runbook em deploy/openobserve/README.md.",
        period_minutes=ABSENCE_PERIOD_MINUTES,
        silence_minutes=60,
        destination=DestinationKey.URGENT,
    ),
    _SpanCause(
        "causa-sem-traces",
        "Nenhum evento chegou ao stream de traces em 30 min: confira a exportação da aplicação e "
        "o Collector. Veja o runbook em deploy/openobserve/README.md.",
        period_minutes=ABSENCE_PERIOD_MINUTES,
        silence_minutes=60,
        destination=DestinationKey.URGENT,
    ),
)


def alerts(stream: str, prefix: str) -> list[Alert]:
    """Todos os alertas: queima por SLO e nível, causas, ausência de telemetria e o heartbeat."""
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
        *(_span_alert(cause, stream, prefix) for cause in SPAN_CAUSES),
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


def _span_alert(cause: _SpanCause, stream: str, prefix: str) -> Alert:
    return Alert(
        name=f"{prefix}{cause.name}",
        description=cause.description,
        stream_type="traces",
        stream_name=stream,
        sql=_render(f"{cause.name}.sql", stream=stream),
        period_minutes=cause.period_minutes,
        frequency_minutes=_SPAN_CAUSE_FREQUENCY_MINUTES,
        silence_minutes=cause.silence_minutes,
        destination=cause.destination,
    )


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
