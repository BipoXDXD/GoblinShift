"""Espera do gateway pelo Nginx do GoblinShift durante a troca de containers (ADR 044).

Sobe a imagem do `gateway.json` com a configuração versionada e um destino descartável com IP fixo,
como o Nginx em produção. Tirar o destino da rede reproduz a troca: o endereço deixa de responder
e volta com outro container. O destino registra o que recebeu, então a contagem por caminho prova
quantas vezes o gateway entregou cada pedido.
"""

import http.client
import json
import re
import socket
import ssl
import subprocess
import time
import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from ipaddress import ip_network
from pathlib import Path

import pytest

from tests.test_gateway_client_ip import (
    DIRECT_V4,
    DOCKER_TIMEOUT_SECONDS,
    GOBLINSHIFT,
    SITES,
    STILLON,
    _copy_gateway_config,
    _docker,
    _gateway_image,
    _proxy_header,
    _replace_once,
)

SITE = SITES / f"{GOBLINSHIFT}.caddy"
WAIT_SECONDS = 30
# Teto de leitura da Cloudflare fora do plano Enterprise: esperar mais perderia a resposta.
CLOUDFLARE_READ_TIMEOUT_SECONDS = 125
# Mais que a espera anterior somada à última tentativa de conexão: com ela, o pedido falharia.
OUTAGE_SECONDS = 10
# A última tentativa começa antes do fim da espera e tem o tempo de conexão padrão do Caddy.
WAIT_OVERRUN_SECONDS = 5
SOCKET_TIMEOUT_SECONDS = WAIT_SECONDS + 2 * WAIT_OVERRUN_SECONDS
READY_TIMEOUT_SECONDS = 30
READY_POLL_SECONDS = 0.2
BACKEND_HOST_INDEX = 250
UPLOAD = b"x" * (5 * 1024 * 1024)
APPLICATION_ERROR = "falha da aplicacao"
FALLBACK_BODY = "Serviço temporariamente indisponível. Tente novamente em instantes."
BACKEND_CADDYFILE = f"""\
:80 {{
 log {{
  output stdout
  format json
 }}
 abort /em-voo/*
 respond /erro-da-aplicacao "{APPLICATION_ERROR}" 503
 respond "corpo={{http.request.body}}"
}}
"""


@dataclass(frozen=True)
class Reply:
    status: int
    headers: dict[str, str]
    body: bytes
    seconds: float


@dataclass(frozen=True)
class Stack:
    network: str
    backend: str
    backend_ip: str
    backend_config: Path
    port: int
    image: str

    def start_backend(self) -> None:
        _docker(
            "run", "-d", "--name", self.backend, "--network", self.network,
            "--ip", self.backend_ip, "-v", f"{self.backend_config}:/etc/caddy:ro", self.image,
        )  # fmt: skip

    def remove_backend(self) -> None:
        _docker("rm", "--force", self.backend)

    def received(self, path: str) -> int:
        """Quantos pedidos a `path` o destino recebeu."""
        logs = subprocess.run(  # noqa: S603 - argumentos montados pelo teste, sem shell
            ["docker", "logs", self.backend],  # noqa: S607
            capture_output=True,
            text=True,
            check=True,
            timeout=DOCKER_TIMEOUT_SECONDS,
        )
        entries = [json.loads(line) for line in logs.stdout.splitlines() if line.startswith("{")]
        return sum(entry.get("request", {}).get("uri") == path for entry in entries)

    def send(
        self,
        method: str,
        path: str,
        body: bytes = b"",
        host: str = GOBLINSHIFT,
        extra_headers: tuple[str, ...] = (),
    ) -> Reply:
        """Faz um pedido pelo gateway e devolve a resposta e quanto ela demorou."""
        lines = [
            f"{method} {path} HTTP/1.1",
            f"Host: {host}",
            "Connection: close",
            f"Content-Length: {len(body)}",
            *extra_headers,
            "",
            "",
        ]
        # O certificado vem da CA descartável do container de teste, só em 127.0.0.1.
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        started = time.monotonic()
        with socket.create_connection(("127.0.0.1", self.port), SOCKET_TIMEOUT_SECONDS) as raw:
            raw.sendall(_proxy_header(DIRECT_V4))
            with context.wrap_socket(raw, server_hostname=host) as tls:
                tls.sendall("\r\n".join(lines).encode() + body)
                response = http.client.HTTPResponse(tls)
                response.begin()
                content = response.read()
        headers = {name.lower(): value for name, value in response.getheaders()}
        return Reply(response.status, headers, content, time.monotonic() - started)

    def wait_until_ready(self) -> None:
        deadline = time.monotonic() + READY_TIMEOUT_SECONDS
        while True:
            try:
                if self.send("GET", "/pronto").status == http.client.OK:
                    return
            except OSError, http.client.HTTPException:
                pass
            if time.monotonic() >= deadline:
                raise TimeoutError("gateway de teste não ficou pronto")
            time.sleep(READY_POLL_SECONDS)


def _unused_subnet(network: str) -> str:
    """Faixa livre escolhida pelo Docker: `--ip` só vale em rede com faixa declarada."""
    _docker("network", "create", network)
    try:
        return _docker(
            "network", "inspect", "-f", "{{(index .IPAM.Config 0).Subnet}}", network
        ).strip()
    finally:
        _docker("network", "rm", network)


def _point_site_at(config: Path, address: str) -> None:
    site = config / "conf.d" / SITE.name
    site.write_text(_replace_once(site.read_text(), "eco:80", f"{address}:80"))


@pytest.fixture(scope="module")
def stack(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Stack]:
    workdir = tmp_path_factory.mktemp("gateway-wait")
    suffix = uuid.uuid4().hex[:8]
    network = f"gs-test-wait-{suffix}"
    backend = f"gs-test-wait-backend-{suffix}"
    proxy = f"gs-test-wait-proxy-{suffix}"
    image = _gateway_image()
    subnet = _unused_subnet(network)
    backend_ip = str(ip_network(subnet)[BACKEND_HOST_INDEX])
    backend_config = workdir / "destino"
    backend_config.mkdir()
    (backend_config / "Caddyfile").write_text(BACKEND_CADDYFILE)
    _copy_gateway_config(workdir / "caddy", subnet)
    _point_site_at(workdir / "caddy", backend_ip)
    _docker("network", "create", "--subnet", subnet, network)
    try:
        _docker(
            "run", "-d", "--name", proxy, "--network", network, "-p", "127.0.0.1::443",
            "-v", f"{workdir / 'caddy'}:/etc/caddy:ro", image,
        )  # fmt: skip
        published = _docker("port", proxy, "443/tcp").splitlines()[0].rsplit(":", 1)[1]
        running = Stack(network, backend, backend_ip, backend_config, int(published), image)
        running.start_backend()
        running.wait_until_ready()
        yield running
    finally:
        subprocess.run(  # noqa: S603 - nomes gerados pelo teste, sem shell
            ["docker", "rm", "--force", backend, proxy],  # noqa: S607
            capture_output=True,
            check=False,
            timeout=DOCKER_TIMEOUT_SECONDS,
        )
        _docker("network", "rm", network)


@dataclass(frozen=True)
class Delivery:
    reply: Reply
    received: int


CONTAINER_SWAP_REQUESTS: dict[str, tuple[str, bytes]] = {
    "GET": ("GET", b""),
    "POST": ("POST", b'{"nome":"teste"}'),
    "POST-grande": ("POST", UPLOAD),
    "PUT": ("PUT", b'{"nome":"teste"}'),
}


@pytest.fixture(scope="module")
def container_swap(stack: Stack) -> dict[str, Delivery]:
    """Pedidos que chegam com o destino fora e terminam depois que outro container o substitui."""
    paths = {name: f"/troca/{name}" for name in CONTAINER_SWAP_REQUESTS}
    stack.remove_backend()
    with ThreadPoolExecutor(len(paths)) as pool:
        pending = {
            name: pool.submit(stack.send, method, paths[name], body)
            for name, (method, body) in CONTAINER_SWAP_REQUESTS.items()
        }
        time.sleep(OUTAGE_SECONDS)
        stack.start_backend()
        replies = {name: future.result() for name, future in pending.items()}
    return {name: Delivery(replies[name], stack.received(paths[name])) for name in paths}


@pytest.fixture(scope="module")
def exhausted(stack: Stack) -> dict[str, Reply]:
    """Pedidos que esgotam a espera, com o destino fora do ar até o fim."""
    stack.remove_backend()
    with ThreadPoolExecutor() as pool:
        pending = {
            "GET": pool.submit(stack.send, "GET", "/fora"),
            "POST": pool.submit(stack.send, "POST", "/fora", b'{"nome":"teste"}'),
            "stillon": pool.submit(stack.send, "GET", "/fora", host=STILLON),
        }
        replies = {name: future.result() for name, future in pending.items()}
    stack.start_backend()
    stack.wait_until_ready()
    return replies


def _site_directives() -> str:
    lines = SITE.read_text().splitlines()
    return "\n".join(line for line in lines if not line.lstrip().startswith("#"))


# --- Contrato do arquivo -----------------------------------------------------------------------


def test_espera_configurada_fica_abaixo_do_limite_de_leitura_da_cloudflare() -> None:
    match = re.search(r"^\s*lb_try_duration (\d+)s$", _site_directives(), re.MULTILINE)

    assert match is not None
    assert int(match[1]) == WAIT_SECONDS
    assert int(match[1]) < CLOUDFLARE_READ_TIMEOUT_SECONDS


@pytest.mark.parametrize("directive", ["lb_retry_match", "lb_retries"])
def test_site_nao_amplia_o_que_o_caddy_repete(directive: str) -> None:
    assert directive not in _site_directives()


# --- Gateway real ------------------------------------------------------------------------------


@pytest.mark.gateway
@pytest.mark.parametrize("name", CONTAINER_SWAP_REQUESTS)
def test_pedido_durante_a_troca_espera_o_container_novo(
    container_swap: dict[str, Delivery], name: str
) -> None:
    delivery = container_swap[name]
    _, body = CONTAINER_SWAP_REQUESTS[name]

    assert delivery.reply.status == http.client.OK
    assert delivery.reply.body == b"corpo=" + body
    assert OUTAGE_SECONDS <= delivery.reply.seconds < WAIT_SECONDS


@pytest.mark.gateway
@pytest.mark.parametrize("name", CONTAINER_SWAP_REQUESTS)
def test_pedido_durante_a_troca_chega_uma_vez(
    container_swap: dict[str, Delivery], name: str
) -> None:
    assert container_swap[name].received == 1


@pytest.mark.gateway
@pytest.mark.parametrize("name", ["GET", "POST"])
def test_esgotada_a_espera_responde_503(exhausted: dict[str, Reply], name: str) -> None:
    reply = exhausted[name]

    assert reply.status == http.client.SERVICE_UNAVAILABLE
    assert WAIT_SECONDS <= reply.seconds <= WAIT_SECONDS + WAIT_OVERRUN_SECONDS


@pytest.mark.gateway
def test_503_pede_nova_tentativa_e_nao_entra_em_cache(exhausted: dict[str, Reply]) -> None:
    headers = exhausted["GET"].headers

    assert headers["retry-after"] == str(WAIT_SECONDS)
    assert headers["cache-control"] == "no-store"
    assert headers["content-type"] == "text/plain; charset=utf-8"


@pytest.mark.gateway
@pytest.mark.parametrize("name", ["GET", "POST"])
def test_503_nao_revela_o_proxy(exhausted: dict[str, Reply], name: str) -> None:
    reply = exhausted[name]

    assert reply.body.decode() == FALLBACK_BODY
    assert set(reply.headers) == {
        "cache-control",
        "connection",
        "content-length",
        "content-type",
        "date",
        "retry-after",
    }


@pytest.mark.gateway
def test_stillon_nao_ganha_a_espera_nem_o_503(exhausted: dict[str, Reply]) -> None:
    reply = exhausted["stillon"]

    assert reply.status == http.client.BAD_GATEWAY
    assert reply.body == b""
    assert reply.seconds < OUTAGE_SECONDS


@pytest.mark.gateway
@pytest.mark.parametrize(
    ("method", "body", "extra_headers"),
    [
        pytest.param("POST", b'{"nome":"teste"}', (), id="POST"),
        pytest.param(
            "POST",
            b'{"nome":"teste"}',
            ("Idempotency-Key: chave-de-teste-123456",),
            id="POST-com-idempotency-key",
        ),
        pytest.param("PUT", b'{"nome":"teste"}', (), id="PUT"),
        pytest.param("PATCH", b'{"nome":"teste"}', (), id="PATCH"),
        pytest.param("DELETE", b"", (), id="DELETE"),
    ],
)
def test_pedido_que_chegou_ao_destino_nao_e_reenviado(
    stack: Stack, method: str, body: bytes, extra_headers: tuple[str, ...]
) -> None:
    path = f"/em-voo/{uuid.uuid4().hex}"
    # Deixa uma conexão ociosa no gateway: reaproveitá-la é o caso em que o transporte poderia
    # repetir o pedido sozinho.
    assert stack.send("GET", "/aquece").status == http.client.OK

    reply = stack.send(method, path, body, extra_headers=extra_headers)

    assert reply.status == http.client.SERVICE_UNAVAILABLE
    assert reply.body.decode() == FALLBACK_BODY
    assert reply.seconds < OUTAGE_SECONDS
    assert stack.received(path) == 1


@pytest.mark.gateway
def test_erro_da_aplicacao_passa_sem_troca(stack: Stack) -> None:
    reply = stack.send("GET", "/erro-da-aplicacao")

    assert reply.status == http.client.SERVICE_UNAVAILABLE
    assert reply.body.decode() == APPLICATION_ERROR
    assert "retry-after" not in reply.headers
