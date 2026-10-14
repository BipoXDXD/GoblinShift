"""OpenObserve real (a imagem fixada no compose.yaml), Collector real e um receptor de webhooks.

Uma instância por sessão de testes; cada teste usa streams e prefixos próprios, então não há
estado compartilhado entre eles. Os spans sintéticos saem pelo mesmo exportador OTLP da
aplicação, com os atributos que o evento amplo grava (o contrato é conferido em
tests/api/test_wide_event.py).

Diferenças do ambiente de produção, só aqui:
- `ZO_SKIP_SSRF_CHECKS`: o receptor de webhooks roda no host do teste, num IP privado, que o
  OpenObserve recusaria (ADR 013);
- o Collector lê dois tmpfs pequenos nos pontos de montagem de produção, um deles cheio acima
  de 85%, e coleta a cada 5 s.
"""

import base64
import json
import secrets
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
import yaml
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import SpanKind
from opentelemetry.util.types import AttributeValue
from testcontainers.core.container import DockerContainer
from testcontainers.core.network import Network

from openobserve.catalog import DISK_STREAM
from openobserve.client import Credentials, OpenObserve

ROOT = Path(__file__).resolve().parents[2]
COLLECTOR_CONFIG = ROOT / "docker" / "otel-collector" / "config.yaml"
BUSYBOX_IMAGE = (
    "busybox:1.37.0@sha256:bdf57e528e45e4433820e045b29b4597825a1c9e38353532d90a01445013f82e"
)
O2_PORT = 5080
CREDENTIALS = Credentials(email="root@goblinshift.test", password="Teste#" + secrets.token_hex(8))
READY_TIMEOUT_S = 90.0
POLL_S = 0.5
HTTP_OK = 200
US_PER_S = 1_000_000
NS_PER_MS = 1_000_000
# O tmpfs da telemetria fica 88% cheio; o do storage, vazio.
RUNS_ROUTE = "/api/v1/runs"
DOWNLOAD_ROUTE = "/api/v1/artifacts/{artifact_id}/download"
FULL_MOUNT = "/hostfs/telemetry"
EMPTY_MOUNT = "/hostfs/storage"
TMPFS_KIB = 1024
FILL_KIB = 900


def compose_image(service: str) -> str:
    """A imagem do serviço no compose.yaml: o teste usa a mesma versão da produção."""
    compose = yaml.safe_load((ROOT / "compose.yaml").read_text(encoding="utf-8"))
    image: str = compose["services"][service]["image"]
    return image


def wait_until[T](probe: Callable[[], T | None], what: str, timeout_s: float = 60.0) -> T:
    deadline = time.monotonic() + timeout_s
    while True:
        result = probe()
        if result is not None:
            return result
        if time.monotonic() > deadline:
            raise TimeoutError(f"esperando {what}")
        time.sleep(POLL_S)


def now_us() -> int:
    return time.time_ns() // 1000


# --- Receptor de webhooks ----------------------------------------------------------------------


@dataclass
class Notification:
    path: str
    body: str
    received: float


@dataclass
class WebhookReceiver:
    """Recebe as notificações dos destinos; o OpenObserve chega a ele por host.docker.internal."""

    port: int
    notifications: list[Notification] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def url(self, path: str) -> str:
        return f"http://host.docker.internal:{self.port}/{path}"

    def at(self, path: str, since: float = 0.0) -> list[Notification]:
        with self.lock:
            return [n for n in self.notifications if n.path == f"/{path}" and n.received >= since]

    def record(self, path: str, body: str) -> None:
        with self.lock:
            self.notifications.append(Notification(path, body, time.monotonic()))


@pytest.fixture(scope="session")
def receiver() -> Iterator[WebhookReceiver]:
    holder: list[WebhookReceiver] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("content-length", "0"))
            holder[0].record(self.path, self.rfile.read(length).decode("utf-8", "replace"))
            self.send_response(HTTP_OK)
            self.end_headers()

        def log_message(self, format: str, *args: Any) -> None:
            return

    # Todas as interfaces: no Linux da CI, o container chega ao host pelo gateway da bridge.
    server = ThreadingHTTPServer(("0.0.0.0", 0), Handler)  # noqa: S104 (só durante o teste)
    holder.append(WebhookReceiver(port=server.server_address[1]))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield holder[0]
    finally:
        server.shutdown()
        server.server_close()


# --- OpenObserve e Collector ------------------------------------------------------------------


@dataclass(frozen=True)
class Telemetry:
    api: OpenObserve
    otlp_endpoint: str

    def traces(self, stream: str) -> SpanWriter:
        return SpanWriter(self, stream)

    def count(self, stream: str, where: str = "main = 'true'") -> int:
        rows = self.api.search(
            f'SELECT count(*) AS n FROM "{stream}" WHERE {where}',
            "traces",
            now_us() - 6 * 3600 * US_PER_S,
            now_us() + 60 * US_PER_S,
        )
        return int(rows[0]["n"]) if rows else 0


@pytest.fixture(scope="session")
def telemetry() -> Iterator[Telemetry]:
    with (
        Network() as network,
        _openobserve(network) as o2,
        _filled_volume() as volumes,
        _collector(network, volumes),
    ):
        base_url = f"http://{o2.get_container_host_ip()}:{o2.get_exposed_port(O2_PORT)}"
        api = OpenObserve(base_url, CREDENTIALS)
        wait_until(lambda: True if api.healthy() else None, "OpenObserve", READY_TIMEOUT_S)
        # O alerta de disco só valida depois que o Collector criou o stream de métricas.
        wait_until(
            lambda: True if api.stream_fields(DISK_STREAM, "metrics") else None,
            "métricas de disco do Collector",
            READY_TIMEOUT_S,
        )
        yield Telemetry(api, f"{base_url}/api/default/v1/traces")


def _openobserve(network: Network) -> DockerContainer:
    return (
        DockerContainer(compose_image("openobserve"))
        .with_env("ZO_LOCAL_MODE", "true")
        .with_env("ZO_DATA_DIR", "/data")
        .with_env("ZO_ROOT_USER_EMAIL", CREDENTIALS.email)
        .with_env("ZO_ROOT_USER_PASSWORD", CREDENTIALS.password)
        .with_env("ZO_TELEMETRY", "false")
        .with_env("ZO_SKIP_SSRF_CHECKS", "true")
        .with_exposed_ports(O2_PORT)
        .with_network(network)
        .with_network_aliases("openobserve")
        .with_kwargs(extra_hosts={"host.docker.internal": "host-gateway"})
    )


class _filled_volume:  # noqa: N801 (usado como gerenciador de contexto, como as fixtures)
    """Dois volumes tmpfs; um container busybox enche um deles e os mantém montados.

    Um volume tmpfs some quando o último container que o monta para: o busybox fica de pé
    enquanto o Collector lê os dois.
    """

    def __init__(self) -> None:
        suffix = uuid.uuid4().hex[:8]
        self.names = {FULL_MOUNT: f"gs-test-full-{suffix}", EMPTY_MOUNT: f"gs-test-empty-{suffix}"}
        self.holder: DockerContainer | None = None

    def __enter__(self) -> dict[str, str]:
        client = DockerContainer(BUSYBOX_IMAGE).get_docker_client().client
        for name in self.names.values():
            client.volumes.create(
                name=name,
                driver="local",
                driver_opts={"type": "tmpfs", "device": "tmpfs", "o": f"size={TMPFS_KIB}k"},
            )
        self.holder = (
            DockerContainer(BUSYBOX_IMAGE)
            .with_volume_mapping(self.names[FULL_MOUNT], "/full", "rw")
            .with_volume_mapping(self.names[EMPTY_MOUNT], "/empty", "rw")
            .with_command(
                ["sh", "-c", f"dd if=/dev/zero of=/full/fill bs=1k count={FILL_KIB} && sleep 3600"]
            )
        )
        self.holder.start()
        return self.names

    def __exit__(self, *_exc: object) -> None:
        if self.holder is not None:
            client = self.holder.get_docker_client().client
            self.holder.stop()
            for name in self.names.values():
                client.volumes.get(name).remove(force=True)


def _collector(network: Network, volumes: dict[str, str]) -> DockerContainer:
    auth = base64.b64encode(f"{CREDENTIALS.email}:{CREDENTIALS.password}".encode()).decode()
    container = (
        DockerContainer(compose_image("otel-collector"))
        .with_command(["--config=/etc/otelcol/config.yaml"])
        .with_env("OPENOBSERVE_BASIC_AUTH", auth)
        .with_env("GS_HOSTMETRICS_INTERVAL", "5s")
        .with_volume_mapping(str(COLLECTOR_CONFIG), "/etc/otelcol/config.yaml", "ro")
        .with_network(network)
    )
    for mount, volume in volumes.items():
        container = container.with_volume_mapping(volume, mount, "ro")
    return container


# --- Spans sintéticos -------------------------------------------------------------------------


class SpanWriter:
    """Escreve spans num stream próprio, com instante e atributos escolhidos pelo teste."""

    def __init__(self, telemetry: Telemetry, stream: str) -> None:
        auth = base64.b64encode(f"{CREDENTIALS.email}:{CREDENTIALS.password}".encode()).decode()
        exporter = OTLPSpanExporter(
            endpoint=telemetry.otlp_endpoint,
            headers={"Authorization": f"Basic {auth}", "stream-name": stream},
        )
        self.provider = TracerProvider(
            resource=Resource.create(
                {"service.name": "goblinshift-api", "deployment.environment": "test"}
            )
        )
        self.provider.add_span_processor(BatchSpanProcessor(exporter))
        self.tracer = self.provider.get_tracer("tests.openobserve")
        self.telemetry = telemetry
        self.stream = stream
        self.written = 0

    def span(
        self, name: str, age_s: float, attributes: dict[str, AttributeValue], duration_ms: float
    ) -> None:
        start_ns = time.time_ns() - int(age_s * 1e9)
        span = self.tracer.start_span(
            name, kind=SpanKind.SERVER, start_time=start_ns, attributes=attributes
        )
        span.end(end_time=start_ns + int(duration_ms * NS_PER_MS))
        self.written += 1

    def request(
        self,
        method: str,
        route: str,
        status: int,
        *,
        age_s: float = 60.0,
        duration_ms: float = 50.0,
        main: bool = True,
        extra: dict[str, AttributeValue] | None = None,
    ) -> None:
        """O evento amplo de uma requisição, com os nomes de atributo da instrumentação."""
        attributes: dict[str, AttributeValue] = {
            "main": main,
            "http.method": method,
            "http.route": route,
            "http.status_code": status,
            "duration_ms": duration_ms,
            **(extra or {}),
        }
        self.span(f"{method} {route}", age_s, attributes, duration_ms)

    def acceptance(
        self,
        status: int,
        *,
        duration_ms: float = 50.0,
        error_code: str | None = None,
        age_s: float = 60.0,
    ) -> None:
        extra: dict[str, AttributeValue] = {"error.code": error_code} if error_code else {}
        self.request("POST", RUNS_ROUTE, status, age_s=age_s, duration_ms=duration_ms, extra=extra)

    def download(
        self,
        status: int,
        *,
        first_byte_ms: float = 20.0,
        completed: bool = True,
        age_s: float = 60.0,
    ) -> None:
        self.request(
            "GET",
            DOWNLOAD_ROUTE,
            status,
            age_s=age_s,
            extra={
                "http.response.first_byte_ms": first_byte_ms,
                "http.response.completed": completed,
            },
        )

    def run_finished(
        self,
        *,
        error_code: str | None = None,
        run_duration_ms: float | None = 1000.0,
        eligible: bool = True,
        main: bool = True,
        age_s: float = 60.0,
    ) -> None:
        """O evento de término da Run, no contrato de deploy/openobserve/README.md."""
        attributes: dict[str, AttributeValue] = {
            "main": main,
            "run.state": "failed" if error_code else "succeeded",
            "slo.execution.eligible": eligible,
        }
        if error_code is not None:
            attributes["error.code"] = error_code
        if run_duration_ms is not None:
            attributes["run.duration_ms"] = run_duration_ms
        self.span("run.finished", age_s, attributes, 1.0)

    def flush(self) -> None:
        """Exporta tudo e espera o OpenObserve devolver todos os spans nas consultas."""
        self.provider.force_flush()
        expected = self.written
        wait_until(
            lambda: True if self.telemetry.count(self.stream, "1 = 1") >= expected else None,
            f"{expected} spans no stream {self.stream}",
        )


def unique_stream(label: str) -> str:
    return f"{label}_{uuid.uuid4().hex[:10]}"


def notification_text(notification: Notification) -> str:
    try:
        return json.dumps(json.loads(notification.body), ensure_ascii=False)
    except ValueError:
        return notification.body
