"""Erros do provisionamento que o OpenObserve dos testes não provoca (ele roda sem SSRF)."""

from typing import Any

import pytest

from openobserve.catalog import DestinationKey, alerts
from openobserve.client import OpenObserveError
from openobserve.provision import ProvisionError, provision

CHANNEL_URL = "https://discord.com/api/webhooks/1/SEGREDO-DO-CANAL"


class RefusingDestination:
    """Aceita tudo, menos o destino, e repete a URL na recusa, como uma API poderia fazer."""

    def stream_fields(self, _stream: str, _stream_type: str) -> set[str]:
        return {"main", "http_method", "http_route", "http_status_code", "error_code"} | {
            "duration_ms",
            "run_duration_ms",
            "slo_execution_eligible",
            "http_response_first_byte_ms",
            "http_response_completed",
        }

    def search(self, *_args: Any) -> list[dict[str, Any]]:
        return []

    def save_template(self, _name: str, _body: str) -> bool:
        return True

    def save_destination(self, name: str, url: str, _template: str) -> bool:
        raise OpenObserveError(f"POST /destinations: HTTP 400: destino {name} inválido: {url}")


def test_destino_recusado_nao_repete_a_url_na_mensagem() -> None:
    heartbeat = next(a for a in alerts(stream="s", prefix="t-") if a.name == "t-heartbeat")

    with pytest.raises(ProvisionError) as error:
        provision(
            RefusingDestination(),  # type: ignore[arg-type]  # stub só do que o caminho usa
            alerts=[heartbeat],
            destinations={DestinationKey.HEARTBEAT: CHANNEL_URL},
            prefix="t-",
        )

    assert "t-heartbeat" in str(error.value)
    assert "SEGREDO" not in str(error.value)
