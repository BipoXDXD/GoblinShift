"""O receptor OTLP do Collector só aceita quem tem o token e limita o corpo (#442, 31j; D3).

O Nginx também está na rede interna: sem token, quem a alcançasse injetaria spans e logs falsos
nos alertas. Sobe o Collector real com o `config.yaml` do repositório e os segredos montados
como em produção; o OpenObserve não precisa estar de pé (o receptor responde antes do export).
"""

import http.client
from collections.abc import Iterator
from pathlib import Path

import pytest
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
from testcontainers.core.container import DockerContainer

from tests.openobserve.conftest import (
    COLLECTOR_CONFIG,
    OTLP_TOKEN,
    collector_secret_mounts,
    compose_image,
    compose_privileges,
    wait_until,
    write_secret_files,
)

pytestmark = pytest.mark.openobserve

OTLP_PORT = 4318
TRACES = "/v1/traces"
PROTOBUF = {"Content-Type": "application/x-protobuf"}
HTTP_OK = 200
HTTP_UNAUTHORIZED = 401
TOO_LARGE = {400, 413}
OVERSIZED_BYTES = 9 * 1024 * 1024  # acima dos 8 MiB do max_request_body_size
TIMEOUT_S = 10


@pytest.fixture(scope="module")
def collector(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[str, int]]:
    secrets_dir = tmp_path_factory.mktemp("segredos-collector")
    secrets_dir.chmod(0o755)
    container = collector_secret_mounts(
        DockerContainer(compose_image("otel-collector"))
        .with_command(["--config=/etc/otelcol/config.yaml"])
        .with_volume_mapping(str(COLLECTOR_CONFIG), "/etc/otelcol/config.yaml", "ro")
        .with_exposed_ports(OTLP_PORT)
        .with_kwargs(**compose_privileges("otel-collector")),
        write_secret_files(secrets_dir),
    )
    with container:
        address = (container.get_container_host_ip(), int(container.get_exposed_port(OTLP_PORT)))
        wait_until(lambda: _accepting(address), "receptor OTLP do Collector")
        yield address


def _accepting(address: tuple[str, int]) -> bool | None:
    try:
        _post(address, b"", {})
    except OSError:
        return None
    return True


def _post(address: tuple[str, int], body: bytes, headers: dict[str, str]) -> int:
    connection = http.client.HTTPConnection(*address, timeout=TIMEOUT_S)
    try:
        connection.request("POST", TRACES, body=body, headers={**PROTOBUF, **headers})
        return connection.getresponse().status
    finally:
        connection.close()


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_collector_recusa_post_sem_token(collector: tuple[str, int]) -> None:
    assert _post(collector, b"", {}) == HTTP_UNAUTHORIZED


def test_collector_recusa_token_errado(collector: tuple[str, int]) -> None:
    assert _post(collector, b"", _bearer("x" * 64)) == HTTP_UNAUTHORIZED


def test_collector_aceita_o_token_do_arquivo(collector: tuple[str, int]) -> None:
    # Corpo vazio é um ExportTraceServiceRequest válido, sem spans.
    assert _post(collector, b"", _bearer(OTLP_TOKEN)) == HTTP_OK


def _oversized_request() -> bytes:
    """Protobuf válido acima do limite: sem o teto, o Collector o aceitaria com 200."""
    request = ExportTraceServiceRequest()
    attribute = request.resource_spans.add().resource.attributes.add()
    attribute.key = "enchimento"
    attribute.value.string_value = "x" * OVERSIZED_BYTES
    body: bytes = request.SerializeToString()
    return body


def test_collector_recusa_corpo_acima_do_limite(collector: tuple[str, int]) -> None:
    try:
        status = _post(collector, _oversized_request(), _bearer(OTLP_TOKEN))
    except ConnectionResetError, BrokenPipeError:
        return  # o Collector pode fechar a conexão no meio do envio: também é recusa
    assert status in TOO_LARGE


def test_segredos_montados_nao_tem_quebra_no_basic_auth(tmp_path: Path) -> None:
    files = write_secret_files(tmp_path)

    assert not files["openobserve_basic_auth"].read_text().endswith("\n")
