"""Provisionamento idempotente pela API do OpenObserve real."""

import uuid
from collections.abc import Iterator
from dataclasses import replace

import pytest

from openobserve.catalog import SLOS, Alert, DestinationKey, alerts
from openobserve.provision import ProvisionError, provision
from tests.openobserve.conftest import Telemetry, WebhookReceiver, unique_stream

pytestmark = pytest.mark.openobserve


def destinations(receiver: WebhookReceiver, prefix: str) -> dict[DestinationKey, str]:
    return {key: receiver.url(f"{prefix}{key.value}") for key in DestinationKey}


def destination_url(telemetry: Telemetry, name: str) -> str:
    stored = telemetry.api.destination(name)
    assert stored is not None, name
    url: str = stored["url"]
    return url


@pytest.fixture
def prefix(telemetry: Telemetry) -> Iterator[str]:
    """Prefixo só deste teste; no fim, nenhum alerta dele fica agendado."""
    value = f"t{uuid.uuid4().hex[:8]}-"
    yield value
    provision(telemetry.api, alerts=[], destinations={}, prefix=value)


def test_cria_template_destinos_e_todos_os_alertas(
    telemetry: Telemetry, receiver: WebhookReceiver, prefix: str
) -> None:
    stream = unique_stream("prov")
    catalog = alerts(stream=stream, prefix=prefix)

    provision(
        telemetry.api, alerts=catalog, destinations=destinations(receiver, prefix), prefix=prefix
    )

    managed = telemetry.api.alerts_named(prefix)
    assert set(managed) == {alert.name for alert in catalog}
    for alert in catalog:
        stored = telemetry.api.alert(managed[alert.name])
        assert stored["query_condition"]["sql"] == alert.sql
        assert stored["stream_type"] == alert.stream_type
        assert stored["stream_name"] == alert.stream_name
        assert stored["destinations"] == [f"{prefix}{alert.destination.value}"]
        assert stored["trigger_condition"]["period"] == alert.period_minutes
        assert stored["trigger_condition"]["frequency"] == alert.frequency_minutes
        assert stored["enabled"] is True
    for key in DestinationKey:
        assert destination_url(telemetry, f"{prefix}{key.value}") == receiver.url(
            f"{prefix}{key.value}"
        )


def test_segunda_execucao_atualiza_no_lugar_sem_duplicar(
    telemetry: Telemetry, receiver: WebhookReceiver, prefix: str
) -> None:
    stream = unique_stream("prov")
    catalog = alerts(stream=stream, prefix=prefix)
    provision(
        telemetry.api, alerts=catalog, destinations=destinations(receiver, prefix), prefix=prefix
    )
    first_ids = telemetry.api.alerts_named(prefix)
    changed = [replace(alert, silence_minutes=alert.silence_minutes + 1) for alert in catalog]
    new_destinations = {**destinations(receiver, prefix), DestinationKey.WARNING: receiver.url("x")}

    provision(telemetry.api, alerts=changed, destinations=new_destinations, prefix=prefix)

    assert telemetry.api.alerts_named(prefix) == first_ids
    for alert in changed:
        stored = telemetry.api.alert(first_ids[alert.name])
        assert stored["trigger_condition"]["silence"] == alert.silence_minutes
    assert destination_url(telemetry, f"{prefix}aviso") == receiver.url("x")


def test_remove_o_alerta_gerenciado_que_saiu_do_catalogo_e_preserva_os_alheios(
    telemetry: Telemetry, receiver: WebhookReceiver, prefix: str
) -> None:
    stream = unique_stream("prov")
    heartbeat = next(a for a in alerts(stream=stream, prefix=prefix) if "heartbeat" in a.name)
    old = replace(heartbeat, name=f"{prefix}velho")
    other_prefix = f"o{uuid.uuid4().hex[:8]}-"
    foreign = replace(heartbeat, name=f"{other_prefix}heartbeat")
    provision(
        telemetry.api,
        alerts=[heartbeat, old],
        destinations=destinations(receiver, prefix),
        prefix=prefix,
    )
    provision(
        telemetry.api,
        alerts=[foreign],
        destinations=destinations(receiver, other_prefix),
        prefix=other_prefix,
    )

    provision(
        telemetry.api,
        alerts=[heartbeat],
        destinations=destinations(receiver, prefix),
        prefix=prefix,
    )

    try:
        assert set(telemetry.api.alerts_named(prefix)) == {heartbeat.name}
        assert set(telemetry.api.alerts_named(other_prefix)) == {foreign.name}
    finally:
        provision(telemetry.api, alerts=[], destinations={}, prefix=other_prefix)


def test_consulta_invalida_falha_antes_de_mudar_qualquer_coisa(
    telemetry: Telemetry, receiver: WebhookReceiver, prefix: str
) -> None:
    stream = unique_stream("prov")
    catalog = alerts(stream=stream, prefix=prefix)
    broken = replace(
        catalog[0],
        name=f"{prefix}quebrado",
        sql=f'SELECT count(*) AS n FROM "{stream}" WHERE coluna_que_nao_existe = 1',
    )

    with pytest.raises(ProvisionError, match=f"{prefix}quebrado"):
        provision(
            telemetry.api,
            alerts=[*catalog, broken],
            destinations=destinations(receiver, prefix),
            prefix=prefix,
        )

    assert telemetry.api.alerts_named(prefix) == {}
    assert telemetry.api.destination(f"{prefix}urgente") is None


def test_destino_que_falta_para_um_alerta_e_recusado(
    telemetry: Telemetry, receiver: WebhookReceiver, prefix: str
) -> None:
    catalog: list[Alert] = alerts(stream=unique_stream("prov"), prefix=prefix)
    partial = {DestinationKey.URGENT: receiver.url(f"{prefix}urgente")}

    with pytest.raises(ProvisionError, match="aviso"):
        provision(telemetry.api, alerts=catalog, destinations=partial, prefix=prefix)


def test_semente_declara_as_colunas_dos_slos_sem_virar_evento(
    telemetry: Telemetry, receiver: WebhookReceiver, prefix: str
) -> None:
    # Num stream novo, as colunas do evento de término da Run ainda não existem, e uma consulta
    # que as cita falharia em toda avaliação. A semente as declara com main = false.
    stream = unique_stream("semente")

    provision(
        telemetry.api,
        alerts=alerts(stream=stream, prefix=prefix),
        destinations=destinations(receiver, prefix),
        prefix=prefix,
    )

    fields = telemetry.api.stream_fields(stream, "traces")
    for slo in SLOS:
        assert set(slo.columns) <= fields, slo.name
    assert telemetry.count(stream) == 0
