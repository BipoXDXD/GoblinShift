"""Alertas agendados de verdade: a queima e a causa disparam sob erro, o normal fica quieto.

Um cenário só, porque o agendador do OpenObserve avalia no máximo a cada minuto: dois streams
recebem tráfego ao mesmo tempo e os alertas de cada um notificam caminhos próprios do receptor.

- "queima": 200 POST /runs nos últimos minutos, 20 deles com 500. São 10% de ruins, 20x o
  orçamento de 0,5%: aviso e urgente da aceitação disparam. O alerta de disco também está
  aqui e vê o tmpfs da telemetria 88% cheio.
- "normal": 200 POST /runs com 1 só ruim (0,5%, exatamente o ritmo permitido, abaixo do aviso
  de 2x), downloads bons e recusas de política. Só o heartbeat notifica.

O teste espera dois heartbeats do "normal" depois das notificações da "queima": cada alerta foi
avaliado pelo menos uma vez com os dados já gravados.
"""

import json
import time
from dataclasses import dataclass, replace

import pytest

from openobserve.catalog import Alert, DestinationKey, alerts
from openobserve.provision import provision
from tests.openobserve.conftest import (
    Notification,
    Telemetry,
    WebhookReceiver,
    notification_text,
    unique_stream,
    wait_until,
)

pytestmark = pytest.mark.openobserve

FIRING_TIMEOUT_S = 240.0
RECENT_S = 3 * 60
BURNING = "queima-"
NORMAL = "normal-"


def fast(catalog: list[Alert]) -> list[Alert]:
    """Avaliação a cada minuto e sem silêncio; consultas e períodos iguais aos de produção."""
    return [replace(alert, frequency_minutes=1, silence_minutes=0) for alert in catalog]


@dataclass(frozen=True)
class Scenario:
    receiver: WebhookReceiver
    started: float

    def at(self, prefix: str, key: DestinationKey) -> list[Notification]:
        return self.receiver.at(f"{prefix}{key.value}", self.started)

    def texts(self, prefix: str, key: DestinationKey) -> str:
        return "\n".join(notification_text(n) for n in self.at(prefix, key))


@pytest.fixture(scope="module")
def scenario(telemetry: Telemetry, receiver: WebhookReceiver) -> Scenario:
    burning, normal = unique_stream("queima"), unique_stream("normal")
    _write_burning_traffic(telemetry, burning)
    _write_normal_traffic(telemetry, normal)
    started = time.monotonic()
    # O alerta de disco lê as métricas do Collector, que são as mesmas para os dois cenários:
    # fica só no da queima.
    normal_catalog = [
        a for a in alerts(stream=normal, prefix=NORMAL) if a.name != f"{NORMAL}causa-disco"
    ]
    for prefix, catalog in (
        (BURNING, alerts(stream=burning, prefix=BURNING)),
        (NORMAL, normal_catalog),
    ):
        provision(
            telemetry.api,
            alerts=fast(catalog),
            destinations={key: receiver.url(f"{prefix}{key.value}") for key in DestinationKey},
            prefix=prefix,
        )
    result = Scenario(receiver, started)

    def burning_notified() -> bool | None:
        urgent = result.texts(BURNING, DestinationKey.URGENT)
        done = "queima-slo-aceitacao-urgente" in urgent and "queima-causa-disco" in urgent
        return True if done else None

    wait_until(burning_notified, "notificações da queima e do disco", FIRING_TIMEOUT_S)
    after = time.monotonic()
    wait_until(
        lambda: True if len(receiver.at(f"{NORMAL}heartbeat", after)) >= 2 else None,
        "duas avaliações do cenário normal",
        FIRING_TIMEOUT_S,
    )
    for prefix in (BURNING, NORMAL):
        provision(telemetry.api, alerts=[], destinations={}, prefix=prefix)
    return result


def _write_burning_traffic(telemetry: Telemetry, stream: str) -> None:
    traces = telemetry.traces(stream)
    for index in range(200):
        if index % 10 == 0:
            traces.acceptance(500, error_code="INTERNAL_ERROR", age_s=RECENT_S)
        else:
            traces.acceptance(202, age_s=RECENT_S)
    traces.flush()


def _write_normal_traffic(telemetry: Telemetry, stream: str) -> None:
    traces = telemetry.traces(stream)
    traces.acceptance(500, error_code="INTERNAL_ERROR", age_s=RECENT_S)
    for _ in range(199):
        traces.acceptance(202, age_s=RECENT_S)
    for _ in range(30):
        traces.acceptance(429, error_code="RATE_LIMITED", age_s=RECENT_S)
        traces.acceptance(503, error_code="QUEUE_FULL", age_s=RECENT_S)
    for _ in range(150):
        traces.download(200, age_s=RECENT_S)
    for _ in range(60):
        traces.run_finished(age_s=RECENT_S)
    traces.flush()


def test_queima_de_aceitacao_dispara_o_urgente_e_o_aviso(scenario: Scenario) -> None:
    assert "queima-slo-aceitacao-urgente" in scenario.texts(BURNING, DestinationKey.URGENT)
    assert "queima-slo-aceitacao-aviso" in scenario.texts(BURNING, DestinationKey.WARNING)


def test_disco_acima_de_85_porcento_dispara_o_alerta_de_causa(scenario: Scenario) -> None:
    assert "queima-causa-disco" in scenario.texts(BURNING, DestinationKey.URGENT)


def test_slos_sem_queima_nao_disparam_no_stream_com_erro(scenario: Scenario) -> None:
    texts = scenario.texts(BURNING, DestinationKey.URGENT) + scenario.texts(
        BURNING, DestinationKey.WARNING
    )

    assert "slo-download" not in texts
    assert "slo-execucao" not in texts
    assert "causa-fila-cheia" not in texts


def test_estado_normal_nao_dispara_nenhum_alerta(scenario: Scenario) -> None:
    assert scenario.at(NORMAL, DestinationKey.URGENT) == []
    assert scenario.at(NORMAL, DestinationKey.WARNING) == []


def test_heartbeat_notifica_a_cada_avaliacao(scenario: Scenario) -> None:
    assert len(scenario.at(NORMAL, DestinationKey.HEARTBEAT)) >= 2


def test_notificacao_e_json_com_texto_para_os_canais_de_chat(scenario: Scenario) -> None:
    body = json.loads(scenario.at(BURNING, DestinationKey.URGENT)[0].body)

    assert set(body) == {"text", "content", "alert"}
    assert body["text"] == body["content"]
    assert body["text"].startswith(f"[GoblinShift] {body['alert']}: ")
    assert "runbook" in body["text"]
