"""Log do gateway Caddy compartilhado sem query, IP completo nem cabeçalhos do cliente (#442).

Com o destino fora do ar (um 502 durante o deploy), o Caddy registra o pedido inteiro no log de
erros. O teste sobe o gateway sem o destino e confere o que sobrou no log do container.
"""

import subprocess
import time
import uuid
from collections.abc import Iterator

import pytest

from tests.gateway_support import (
    DOCKER_TIMEOUT_SECONDS,
    Gateway,
    copy_gateway_config,
    docker,
    gateway_image,
)

pytestmark = pytest.mark.gateway

GOBLINSHIFT = "goblinshift.dev"
STILLON = "stillon.tech"
CLOUDFLARE_V4 = "173.245.48.10"
# O /24 de quem conectou: o endereço fica no log, truncado.
CLOUDFLARE_V4_MASKED = "173.245.48.0"
CLIENT_V4 = "203.0.113.7"
DIRECT_V4 = "198.51.100.20"
READY_TIMEOUT_SECONDS = 30
READY_POLL_SECONDS = 0.2
# O `reverse_proxy` insiste por `lb_try_duration` (5 s) antes de devolver o 502.
UPSTREAM_DOWN_TIMEOUT_SECONDS = 20
LOG_TIMEOUT_SECONDS = 10


def _container_logs(container: str) -> str:
    """Saída e erro do container: o Caddy escreve o log no stderr."""
    result = subprocess.run(  # noqa: S603 - nome gerado pelo teste, sem shell
        ["docker", "logs", container],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
        timeout=DOCKER_TIMEOUT_SECONDS,
    )
    return result.stdout + result.stderr


def _wait_until_ready(gateway: Gateway) -> None:
    deadline = time.monotonic() + READY_TIMEOUT_SECONDS
    while True:
        try:
            response = gateway.request(f"www.{STILLON}", DIRECT_V4)
            assert response.startswith("HTTP/1.1 308 "), response
            return
        except OSError, AssertionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(READY_POLL_SECONDS)


def _logs_with_status(container: str, status: int) -> str:
    deadline = time.monotonic() + LOG_TIMEOUT_SECONDS
    while f'"status":{status}' not in (logs := _container_logs(container)):
        if time.monotonic() >= deadline:
            pytest.fail(f"o gateway não registrou o {status}:\n{logs}")
        time.sleep(READY_POLL_SECONDS)
    return logs


@pytest.fixture(scope="module")
def gateway_sem_upstream(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Gateway]:
    """Gateway com a configuração do repo e sem o container `eco`: o destino falha no DNS."""
    workdir = tmp_path_factory.mktemp("gateway-logs")
    suffix = uuid.uuid4().hex[:8]
    network = f"gs-test-gateway-logs-{suffix}"
    proxy = f"gs-test-gateway-logs-proxy-{suffix}"
    docker("network", "create", network)
    try:
        subnet = docker(
            "network", "inspect", "-f", "{{(index .IPAM.Config 0).Subnet}}", network
        ).strip()
        copy_gateway_config(workdir / "caddy", subnet)
        docker(
            "run", "-d", "--name", proxy, "--network", network, "-p", "127.0.0.1::443",
            "-v", f"{workdir / 'caddy'}:/etc/caddy:ro", gateway_image(),
        )  # fmt: skip
        published = docker("port", proxy, "443/tcp").splitlines()[0].rsplit(":", 1)[1]
        running = Gateway(proxy, int(published))
        _wait_until_ready(running)
        yield running
    finally:
        subprocess.run(  # noqa: S603 - nome gerado pelo teste, sem shell
            ["docker", "rm", "--force", proxy],  # noqa: S607
            capture_output=True,
            check=False,
            timeout=DOCKER_TIMEOUT_SECONDS,
        )
        docker("network", "rm", network)


def test_502_do_gateway_nao_registra_query_ip_nem_cabecalhos_de_cliente(
    gateway_sem_upstream: Gateway,
) -> None:
    canary = f"CANARY-{uuid.uuid4()}"

    response = gateway_sem_upstream.request_path(
        GOBLINSHIFT,
        CLOUDFLARE_V4,
        f"/auth/callback/google?code={canary}&state={canary}",
        f"CF-Connecting-IP: {CLIENT_V4}",
        f"X-Forwarded-For: {CLIENT_V4}",
        f"Referer: https://{GOBLINSHIFT}/auth/callback/google?code={canary}",
        f"Cookie: session={canary}",
        f"Authorization: Bearer {canary}",
        timeout=UPSTREAM_DOWN_TIMEOUT_SECONDS,
    )

    assert response.startswith("HTTP/1.1 502")
    logs = _logs_with_status(gateway_sem_upstream.container, 502)
    for secret in (canary, CLIENT_V4, CLOUDFLARE_V4):
        assert secret not in logs
    assert '"uri":"/auth/callback/google"' in logs.replace(" ", "")
    assert CLOUDFLARE_V4_MASKED in logs
