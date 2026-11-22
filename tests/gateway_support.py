"""Apoio aos testes com o gateway Caddy real (ADR 041).

Sobe a imagem do `gateway.json` com a configuração versionada. A origem de cada conexão vem do
PROXY protocol v2, como o Traefik faz em produção. A cópia da configuração só troca o que depende
do ambiente (emissor e certificado, IP do Traefik, destinos).
"""

import json
import re
import socket
import ssl
import struct
import subprocess
from dataclasses import dataclass
from ipaddress import ip_address
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
COOLIFY = ROOT / "deploy" / "coolify"
GATEWAY = ROOT / "docker" / "gateway"
SITES = GATEWAY / "sites"
DOCKER_TIMEOUT_SECONDS = 60
SOCKET_TIMEOUT_SECONDS = 5
PROXY_V2_SIGNATURE = b"\r\n\r\n\x00\r\nQUIT\n"
PROXY_V2_PROXY_COMMAND = 0x21
PROXY_V2_TCP4 = 0x11
PROXY_V2_TCP6 = 0x21
HTTPS_PORT = 443
SOURCE_PORT = 45678
TEST_ISSUER = " local_certs\n skip_install_trust\n"
ORIGIN_CERT_DIR = "/data/origin-ca"
ORIGIN_CERT_LINE = re.compile(rf"^ *tls {ORIGIN_CERT_DIR}/.*\n", re.MULTILINE)


def docker(*args: str) -> str:
    result = subprocess.run(  # noqa: S603 - argumentos montados pelo teste, sem shell
        ["docker", *args],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
        timeout=DOCKER_TIMEOUT_SECONDS,
    )
    return result.stdout


def gateway_proxy() -> dict[str, Any]:
    gateway = json.loads((COOLIFY / "gateway.json").read_text())
    proxy: dict[str, Any] = gateway["services"]["proxy"]
    return proxy


def gateway_image() -> str:
    image: str = gateway_proxy()["image"]
    return image


def _replace_once(text: str, old: str, new: str) -> str:
    assert old in text, f"{old!r} não encontrado na configuração"
    return text.replace(old, new)


def without_origin_certificate(site: str) -> str:
    return ORIGIN_CERT_LINE.sub("", site)


def copy_gateway_config(target: Path, subnet: str) -> None:
    """Copia a configuração versionada, adaptada só no que depende do ambiente de teste."""
    sites = target / "conf.d"
    sites.mkdir(parents=True)
    caddyfile = (GATEWAY / "Caddyfile").read_text()
    caddyfile = _replace_once(
        caddyfile, " cert_issuer acme {\n  disable_http_challenge\n }\n", TEST_ISSUER
    )
    caddyfile = _replace_once(caddyfile, "allow 172.16.1.6/32", f"allow {subnet}")
    (target / "Caddyfile").write_text(caddyfile)
    for site in SITES.iterdir():
        text = re.sub(r"172\.31\.0\.[34]:80", "eco:80", site.read_text())
        (sites / site.name).write_text(without_origin_certificate(text))


def proxy_header(source: str) -> bytes:
    address = ip_address(source)
    family = PROXY_V2_TCP4 if address.version == 4 else PROXY_V2_TCP6
    destination = ip_address("192.0.2.1" if address.version == 4 else "2001:db8::1")
    addresses = address.packed + destination.packed + struct.pack("!HH", SOURCE_PORT, HTTPS_PORT)
    head = struct.pack("!BBH", PROXY_V2_PROXY_COMMAND, family, len(addresses))
    return PROXY_V2_SIGNATURE + head + addresses


@dataclass(frozen=True)
class Gateway:
    container: str
    port: int

    def request(self, host: str, source: str, *headers: str) -> str:
        """Faz um `GET /` vindo de `source` e devolve a resposta inteira do gateway."""
        return self.request_path(host, source, "/", *headers)

    def request_path(
        self,
        host: str,
        source: str,
        path: str,
        *headers: str,
        timeout: float = SOCKET_TIMEOUT_SECONDS,
    ) -> str:
        """Faz um `GET path` vindo de `source` e devolve a resposta inteira do gateway."""
        lines = [f"GET {path} HTTP/1.1", f"Host: {host}", "Connection: close", *headers, "", ""]
        # O certificado vem da CA descartável do container de teste, só em 127.0.0.1.
        context = ssl.create_default_context()
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        with socket.create_connection(("127.0.0.1", self.port), timeout) as raw:
            raw.sendall(proxy_header(source))
            with context.wrap_socket(raw, server_hostname=host) as tls:
                tls.sendall("\r\n".join(lines).encode())
                response = b""
                while chunk := tls.recv(4096):
                    response += chunk
        return response.decode()

    def echo(self, host: str, source: str, *headers: str) -> dict[str, str]:
        """Faz um pedido vindo de `source` e devolve o que o destino recebeu do gateway."""
        response = self.request(host, source, *headers)
        match = re.search(r"xff=(?P<xff>\S*) cf=(?P<cf>.*)$", response)
        assert match is not None, response
        return match.groupdict()
