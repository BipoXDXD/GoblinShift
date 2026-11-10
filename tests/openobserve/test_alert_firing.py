"""Alertas agendados de verdade: a queima e a causa disparam sob erro, o normal fica quieto.

Um cenário só, porque o agendador do OpenObserve avalia no máximo a cada minuto: três streams
ficam prontos ao mesmo tempo e os alertas de cada um notificam caminhos próprios do receptor.

- "queima": 200 POST /runs nos últimos minutos, 20 deles com 500. São 10% de ruins, 20x o
  orçamento de 0,5%: aviso e urgente da aceitação disparam. O alerta de disco também está
  aqui e vê o tmpfs da telemetria 88% cheio. Há traces, mas nenhuma volta da manutenção.
- "normal": 200 POST /runs com 1 só ruim (0,5%, exatamente o ritmo permitido, abaixo do aviso
  de 2x), downloads bons, recusas de política e uma volta da manutenção. Só o heartbeat notifica.
- "silêncio": só a semente do provisionamento. Os dois alertas de ausência disparam.

O teste espera dois heartbeats do "normal" depois das notificações da "queima": cada alerta foi
avaliado pelo menos uma vez com os dados já gravados.
"""

import json
import time
from dataclasses import dataclass, replace

import pytest

from openobserve.catalog import SPAN_CAUSES, Alert, DestinationKey, alerts
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
# Dois heartbeats depois de um instante qualquer custam até duas voltas do agendador (a primeira
# pode ter acabado de passar), e cada volta leva uns 110-120 s com a carga do cenário: 240 s
# ficavam no limite e falhavam de vez em quando. O prazo cobre duas voltas com 50% de folga.
SCHEDULER_ROUND_S = 120.0
TWO_ROUNDS_TIMEOUT_S = 2 * SCHEDULER_ROUND_S * 1.5
RECENT_S = 3 * 60
BURNING = "queima-"
NORMAL = "normal-"
SILENT = "silencio-"
ABSENCE_CAUSES = ("causa-sem-traces", "causa-manutencao-sem-ciclo")


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
    burning, normal, silent = (unique_stream(s) for s in ("queima", "normal", "silencio"))
    _write_burning_traffic(telemetry, burning)
    _write_normal_traffic(telemetry, normal)
    started = time.monotonic()
    # O alerta de disco lê as métricas do Collector, que são as mesmas para os dois cenários:
    # fica só no da queima.
    normal_catalog = [
        a for a in alerts(stream=normal, prefix=NORMAL) if a.name != f"{NORMAL}causa-disco"
    ]
    # O agendador avalia uma quantidade limitada de alertas por vez: cada alerta volta a cada
    # SCHEDULER_ROUND_S, não a cada minuto. O cenário "queima" não tem span da manutenção, e só o
    # "normal" precisa das causas lidas dele; menos alertas encurtam a volta.
    idle_causes = {
        f"{BURNING}{cause.name}" for cause in SPAN_CAUSES if cause.name not in ABSENCE_CAUSES
    }
    burning_catalog = [
        a for a in alerts(stream=burning, prefix=BURNING) if a.name not in idle_causes
    ]
    silent_names = {f"{SILENT}{name}" for name in ABSENCE_CAUSES}
    silent_catalog = [a for a in alerts(stream=silent, prefix=SILENT) if a.name in silent_names]
    for prefix, catalog in (
        (BURNING, burning_catalog),
        (NORMAL, normal_catalog),
        (SILENT, silent_catalog),
    ):
        provision(
            telemetry.api,
            alerts=fast(catalog),
            destinations={key: receiver.url(f"{prefix}{key.value}") for key in DestinationKey},
            prefix=prefix,
        )
    result = Scenario(receiver, started)

    def burning_notified() -> bool | None:
        burning_urgent = result.texts(BURNING, DestinationKey.URGENT)
        silent_urgent = result.texts(SILENT, DestinationKey.URGENT)
        expected = (
            "queima-slo-aceitacao-urgente" in burning_urgent,
            "queima-causa-disco" in burning_urgent,
            "queima-causa-manutencao-sem-ciclo" in burning_urgent,
            "silencio-causa-sem-traces" in silent_urgent,
            "silencio-causa-manutencao-sem-ciclo" in silent_urgent,
        )
        return True if all(expected) else None

    wait_until(burning_notified, "notificações da queima, do disco e da ausência", FIRING_TIMEOUT_S)
    after = time.monotonic()
    wait_until(
        lambda: True if len(receiver.at(f"{NORMAL}heartbeat", after)) >= 2 else None,
        "duas avaliações do cenário normal",
        TWO_ROUNDS_TIMEOUT_S,
    )
    for prefix in (BURNING, NORMAL, SILENT):
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
    traces.maintenance_cycle(age_s=RECENT_S)
    traces.flush()


def test_queima_de_aceitacao_dispara_o_urgente_e_o_aviso(scenario: Scenario) -> None:
    assert "queima-slo-aceitacao-urgente" in scenario.texts(BURNING, DestinationKey.URGENT)
    assert "queima-slo-aceitacao-aviso" in scenario.texts(BURNING, DestinationKey.WARNING)


def test_disco_acima_de_85_porcento_dispara_o_alerta_de_causa(scenario: Scenario) -> None:
    assert "queima-causa-disco" in scenario.texts(BURNING, DestinationKey.URGENT)


def test_manutencao_parada_dispara_mesmo_com_traces_chegando(scenario: Scenario) -> None:
    urgent = scenario.texts(BURNING, DestinationKey.URGENT)

    assert "queima-causa-manutencao-sem-ciclo" in urgent
    assert "queima-causa-sem-traces" not in urgent


def test_stream_sem_telemetria_dispara_os_dois_alertas_de_ausencia(scenario: Scenario) -> None:
    urgent = scenario.texts(SILENT, DestinationKey.URGENT)

    assert "silencio-causa-sem-traces" in urgent
    assert "silencio-causa-manutencao-sem-ciclo" in urgent


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
