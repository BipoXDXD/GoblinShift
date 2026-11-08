"""IP do cliente no gateway Caddy compartilhado, atrás da Cloudflare (ADR 041).

Sobe a imagem do `gateway.json` com a configuração versionada. A origem de cada conexão vem do
PROXY protocol v2, como o Traefik faz em produção: é ela, e não o que o cliente escreve nos
cabeçalhos, que decide se o `CF-Connecting-IP` vale. A cópia da configuração só troca o que depende
do ambiente (emissor e certificado, IP do Traefik, destinos); as regras de IP são as do repo. O
certificado de origem (ADR 043) tem o próprio gateway, com os sites intactos e certificados
descartáveis.
"""

import json
import re
import socket
import ssl
import struct
import subprocess
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from ipaddress import ip_address, ip_network
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
COOLIFY = ROOT / "deploy" / "coolify"
GATEWAY = ROOT / "docker" / "gateway"
SITES = GATEWAY / "sites"
CLOUDFLARE_LIST = SITES / "cloudflare-ips.conf"
GOBLINSHIFT = "goblinshift.dev"
STILLON = "stillon.tech"
SITES_BEHIND_CLOUDFLARE = (GOBLINSHIFT, STILLON)
DOCKER_TIMEOUT_SECONDS = 60
SOCKET_TIMEOUT_SECONDS = 5
READY_TIMEOUT_SECONDS = 30
READY_POLL_SECONDS = 0.2
PROXY_V2_SIGNATURE = b"\r\n\r\n\x00\r\nQUIT\n"
PROXY_V2_PROXY_COMMAND = 0x21
PROXY_V2_TCP4 = 0x11
PROXY_V2_TCP6 = 0x21
HTTPS_PORT = 443
SOURCE_PORT = 45678

CLOUDFLARE_V4 = "173.245.48.10"
CLOUDFLARE_V6 = "2400:cb00::10"
# Primeiro endereço depois de faixas da lista.
JUST_OUTSIDE_V4 = "173.245.64.0"
JUST_OUTSIDE_V6 = "2400:cb01::"
LAST_INSIDE_V4 = "173.245.63.255"
LAST_INSIDE_V6 = "2400:cb00:ffff:ffff:ffff:ffff:ffff:ffff"
DIRECT_V4 = "198.51.100.20"
DIRECT_V6 = "2001:db8:bad::1"
RETIRED_VPS = "179.197.70.102"
CLIENT_V4 = "203.0.113.7"
CLIENT_V6 = "2001:db8:abcd:12::77"
FORGED = "192.0.2.66"
IPV6_GROUPS = 8
# Formatos que uma classe de caracteres deixaria passar. O Nginx não entende os sem porta: sem IP,
# o $remote_addr seria o gateway, e todos que os enviassem cairiam no mesmo balde do limit_req.
NOT_AN_ADDRESS = (
    "....",
    "deadbeef",
    "203.0.113.7:80",
    "256.1.1.1",
    "203.0.113",
    "203.0.113.07",
    "1:2",
    ":::",
    "2001:db8::1::2",
    "1:2:3:4:5:6:7:8:9",
    "12345::1",
)
ECHO_CADDYFILE = """\
:80 {
 respond "xff={header.X-Forwarded-For} cf={header.CF-Connecting-IP}"
}
"""
TEST_ISSUER = " local_certs\n skip_install_trust\n"
ORIGIN_CERT_DIR = "/data/origin-ca"
ORIGIN_CERT_LINE = re.compile(rf"^ *tls {ORIGIN_CERT_DIR}/.*\n", re.MULTILINE)
CERT_DAYS = "1"


def _docker(*args: str) -> str:
    result = subprocess.run(  # noqa: S603 - argumentos montados pelo teste, sem shell
        ["docker", *args],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
        timeout=DOCKER_TIMEOUT_SECONDS,
    )
    return result.stdout


def _gateway_proxy() -> dict[str, Any]:
    gateway = json.loads((COOLIFY / "gateway.json").read_text())
    proxy: dict[str, Any] = gateway["services"]["proxy"]
    return proxy


def _gateway_image() -> str:
    image: str = _gateway_proxy()["image"]
    return image


def _replace_once(text: str, old: str, new: str) -> str:
    assert old in text, f"{old!r} não encontrado na configuração"
    return text.replace(old, new)


def _without_origin_certificate(site: str) -> str:
    return ORIGIN_CERT_LINE.sub("", site)


def _copy_gateway_config(target: Path, subnet: str) -> None:
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
        (sites / site.name).write_text(_without_origin_certificate(text))


def _proxy_header(source: str) -> bytes:
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
        """Faz um pedido vindo de `source` e devolve a resposta inteira do gateway."""
        lines = ["GET / HTTP/1.1", f"Host: {host}", "Connection: close", *headers, "", ""]
        # O certificado vem da CA descartável do container de teste, só em 127.0.0.1.
        context = ssl.create_default_context()
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        with socket.create_connection(("127.0.0.1", self.port), SOCKET_TIMEOUT_SECONDS) as raw:
            raw.sendall(_proxy_header(source))
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


def _wait_until_ready(gateway: Gateway) -> None:
    deadline = time.monotonic() + READY_TIMEOUT_SECONDS
    while True:
        try:
            gateway.echo(GOBLINSHIFT, DIRECT_V4)
            gateway.echo(STILLON, DIRECT_V4)
            gateway.request(f"www.{STILLON}", DIRECT_V4)
            return
        except OSError, AssertionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(READY_POLL_SECONDS)


@pytest.fixture(scope="module")
def gateway(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Gateway]:
    workdir = tmp_path_factory.mktemp("gateway")
    suffix = uuid.uuid4().hex[:8]
    network = f"gs-test-gateway-{suffix}"
    echo = f"gs-test-echo-{suffix}"
    proxy = f"gs-test-proxy-{suffix}"
    image = _gateway_image()
    (workdir / "eco").mkdir()
    (workdir / "eco" / "Caddyfile").write_text(ECHO_CADDYFILE)
    _docker("network", "create", network)
    try:
        subnet = _docker(
            "network", "inspect", "-f", "{{(index .IPAM.Config 0).Subnet}}", network
        ).strip()
        _copy_gateway_config(workdir / "caddy", subnet)
        _docker(
            "run", "-d", "--name", echo, "--network", network, "--network-alias", "eco",
            "-v", f"{workdir / 'eco'}:/etc/caddy:ro", image,
        )  # fmt: skip
        _docker(
            "run", "-d", "--name", proxy, "--network", network, "-p", "127.0.0.1::443",
            "-v", f"{workdir / 'caddy'}:/etc/caddy:ro", image,
        )  # fmt: skip
        published = _docker("port", proxy, "443/tcp").splitlines()[0].rsplit(":", 1)[1]
        running = Gateway(proxy, int(published))
        _wait_until_ready(running)
        yield running
    finally:
        subprocess.run(  # noqa: S603 - nomes gerados pelo teste, sem shell
            ["docker", "rm", "--force", echo, proxy],  # noqa: S607
            capture_output=True,
            check=False,
            timeout=DOCKER_TIMEOUT_SECONDS,
        )
        _docker("network", "rm", network)


def _cloudflare_ranges() -> list[Any]:
    lines = [
        line
        for line in CLOUDFLARE_LIST.read_text().splitlines()
        if line and not line.startswith("#")
    ]
    assert all(line.startswith("remote_ip ") for line in lines), lines
    return [ip_network(line.removeprefix("remote_ip ")) for line in lines]


# --- Contrato dos arquivos ---------------------------------------------------------------------


def test_lista_versionada_tem_as_duas_familias_e_so_faixas_publicas() -> None:
    ranges = _cloudflare_ranges()

    assert {network.version for network in ranges} == {4, 6}
    assert all(network.is_global for network in ranges)
    assert ip_network("173.245.48.0/20") in ranges
    assert ip_network("2400:cb00::/32") in ranges


def test_lista_nao_e_importada_como_site_pelo_glob_do_gateway() -> None:
    assert "import /etc/caddy/conf.d/*.caddy" in (GATEWAY / "Caddyfile").read_text()
    assert CLOUDFLARE_LIST.suffix != ".caddy"


def test_lista_so_vale_nos_sites_atras_da_cloudflare() -> None:
    for host in SITES_BEHIND_CLOUDFLARE:
        site = (SITES / f"{host}.caddy").read_text()
        assert "import cloudflare-ips.conf\n" in site, host
        assert "header_up -CF-Connecting-IP\n" in site, host
    for path in [GATEWAY / "Caddyfile", COOLIFY / "gateway.json"]:
        text = path.read_text()
        assert "cloudflare-ips.conf" not in text, path
        assert "CF-Connecting-IP" not in text, path


def test_site_do_stillon_tem_a_mesma_regra_do_site_do_goblinshift() -> None:
    goblinshift = (SITES / "goblinshift.dev.caddy").read_text()
    expected = goblinshift.replace(GOBLINSHIFT, STILLON).replace("172.31.0.4:80", "172.31.0.3:80")

    assert (SITES / "stillon.tech.caddy").read_text() == expected


def test_site_do_stillon_mantem_destino_tentativas_e_redirect() -> None:
    site = (SITES / "stillon.tech.caddy").read_text()

    assert "reverse_proxy 172.31.0.3:80 {\n" in site
    assert "lb_try_duration 5s\n" in site
    assert "lb_try_interval 250ms\n" in site
    assert _without_origin_certificate(site).endswith(
        "www.stillon.tech {\n redir https://stillon.tech{uri} 308\n}\n"
    )


def test_a_vps_aposentada_nao_e_mais_proxy_confiavel() -> None:
    caddyfile = (GATEWAY / "Caddyfile").read_text()

    assert RETIRED_VPS not in caddyfile
    assert "trusted_proxies static 172.16.1.6/32\n" in caddyfile


@pytest.mark.parametrize("host", SITES_BEHIND_CLOUDFLARE)
def test_dominio_e_www_usam_o_certificado_de_origem_da_zona(host: str) -> None:
    site = (SITES / f"{host}.caddy").read_text()
    directive = f" tls {ORIGIN_CERT_DIR}/{host}.pem {ORIGIN_CERT_DIR}/{host}.key\n"

    assert site.startswith(f"{host} {{\n{directive}")
    assert f"\nwww.{host} {{\n{directive}" in site
    assert site.count(directive) == 2
    assert len(ORIGIN_CERT_LINE.findall(site)) == 2


def test_certificado_de_origem_nao_muda_o_recurso_do_gateway() -> None:
    assert ORIGIN_CERT_DIR not in (COOLIFY / "gateway.json").read_text()
    assert ORIGIN_CERT_DIR not in (GATEWAY / "Caddyfile").read_text()


def _address_pattern() -> re.Pattern[str]:
    site = (SITES / "goblinshift.dev.caddy").read_text()
    match = re.search(r"vars_regexp \{header\.CF-Connecting-IP\} `(?P<regex>[^`]+)`", site)
    assert match is not None
    return re.compile(match["regex"])


def _every_compression() -> list[str]:
    """Todo `::` possível num endereço de 8 grupos: a parte difícil da regex de IPv6."""
    groups = [f"{index:x}" for index in range(1, IPV6_GROUPS + 1)]
    return [
        ":".join(groups[:start]) + "::" + ":".join(groups[start + length :])
        for length in range(1, IPV6_GROUPS + 1)
        for start in range(IPV6_GROUPS - length + 1)
    ]


@pytest.mark.parametrize("address", _every_compression())
def test_regex_aceita_toda_forma_comprimida_de_ipv6(address: str) -> None:
    assert ip_address(address).version == 6
    assert _address_pattern().search(address)


@pytest.mark.parametrize(
    "address", ["0.1.2.3", "9.9.9.9", "10.99.100.199", "200.249.250.255", "255.255.255.255"]
)
def test_regex_aceita_ipv4_nas_fronteiras_dos_octetos(address: str) -> None:
    assert _address_pattern().search(address)


@pytest.mark.parametrize("value", NOT_AN_ADDRESS)
def test_regex_recusa_o_que_nao_e_endereco(value: str) -> None:
    assert _address_pattern().search(value) is None


# --- Gateway real ------------------------------------------------------------------------------

each_site = pytest.mark.parametrize("host", SITES_BEHIND_CLOUDFLARE)


def _issue_certificate(directory: Path, host: str) -> None:
    command = [
        "openssl", "req", "-x509", "-nodes", "-newkey", "ec",
        "-pkeyopt", "ec_paramgen_curve:prime256v1", "-days", CERT_DAYS, "-subj", f"/CN={host}",
        "-addext", f"subjectAltName=DNS:{host},DNS:*.{host}",
        "-keyout", str(directory / f"{host}.key"), "-out", str(directory / f"{host}.pem"),
    ]  # fmt: skip
    subprocess.run(  # noqa: S603 - argumentos montados pelo teste, sem shell
        command, capture_output=True, check=True, timeout=DOCKER_TIMEOUT_SECONDS
    )


def _served_certificate(port: int, host: str) -> bytes:
    """Certificado que o gateway entrega no handshake com este SNI, em DER."""
    # Certificado autoassinado do teste: quem confere é a comparação com o arquivo.
    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    with socket.create_connection(("127.0.0.1", port), SOCKET_TIMEOUT_SECONDS) as raw:
        raw.sendall(_proxy_header(CLOUDFLARE_V4))
        with context.wrap_socket(raw, server_hostname=host) as tls:
            certificate = tls.getpeercert(binary_form=True)
    assert certificate is not None
    return certificate


def _wait_for_certificate(port: int, host: str) -> None:
    deadline = time.monotonic() + READY_TIMEOUT_SECONDS
    while True:
        try:
            _served_certificate(port, host)
            return
        except OSError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(READY_POLL_SECONDS)


@dataclass(frozen=True)
class OriginGateway:
    """Gateway com os sites intactos e certificados descartáveis no caminho do volume."""

    port: int
    config: Path
    certificates: Path

    def validate(self, *mounts: str) -> subprocess.CompletedProcess[str]:
        """Roda o `caddy validate` da configuração num container novo, com estas montagens."""
        command = [
            "docker", "run", "--rm", "--network", "none",
            "-v", f"{self.config}:/etc/caddy:ro", *mounts, _gateway_image(),
            "caddy", "validate", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile",
        ]  # fmt: skip
        return subprocess.run(  # noqa: S603 - argumentos montados pelo teste, sem shell
            command, capture_output=True, text=True, check=False, timeout=DOCKER_TIMEOUT_SECONDS
        )


@pytest.fixture(scope="module")
def origin_gateway(tmp_path_factory: pytest.TempPathFactory) -> Iterator[OriginGateway]:
    workdir = tmp_path_factory.mktemp("origem")
    config = workdir / "caddy"
    certificates = workdir / "origin-ca"
    certificates.mkdir()
    for host in SITES_BEHIND_CLOUDFLARE:
        _issue_certificate(certificates, host)
    suffix = uuid.uuid4().hex[:8]
    network = f"gs-test-origem-{suffix}"
    proxy = f"gs-test-origem-proxy-{suffix}"
    _docker("network", "create", network)
    try:
        subnet = _docker(
            "network", "inspect", "-f", "{{(index .IPAM.Config 0).Subnet}}", network
        ).strip()
        _copy_gateway_config(config, subnet)
        for site in SITES.glob("*.caddy"):
            (config / "conf.d" / site.name).write_text(site.read_text())
        _docker(
            "run", "-d", "--name", proxy, "--network", network, "-p", "127.0.0.1::443",
            "-v", f"{config}:/etc/caddy:ro",
            "-v", f"{certificates}:{ORIGIN_CERT_DIR}:ro", _gateway_image(),
        )  # fmt: skip
        port = int(_docker("port", proxy, "443/tcp").splitlines()[0].rsplit(":", 1)[1])
        _wait_for_certificate(port, GOBLINSHIFT)
        yield OriginGateway(port, config, certificates)
    finally:
        subprocess.run(  # noqa: S603 - nomes gerados pelo teste, sem shell
            ["docker", "rm", "--force", proxy],  # noqa: S607
            capture_output=True,
            check=False,
            timeout=DOCKER_TIMEOUT_SECONDS,
        )
        _docker("network", "rm", network)


@pytest.mark.gateway
@each_site
@pytest.mark.parametrize("prefix", ["", "www."], ids=["dominio", "www"])
def test_gateway_entrega_o_certificado_de_origem(
    origin_gateway: OriginGateway, host: str, prefix: str
) -> None:
    expected = (origin_gateway.certificates / f"{host}.pem").read_text()

    served = _served_certificate(origin_gateway.port, f"{prefix}{host}")

    assert served == ssl.PEM_cert_to_DER_cert(expected)


@pytest.mark.gateway
def test_configuracao_com_os_arquivos_do_certificado_e_valida(
    origin_gateway: OriginGateway,
) -> None:
    result = origin_gateway.validate("-v", f"{origin_gateway.certificates}:{ORIGIN_CERT_DIR}:ro")

    assert result.returncode == 0, result.stderr


@pytest.mark.gateway
def test_configuracao_sem_os_arquivos_do_certificado_e_recusada(
    origin_gateway: OriginGateway,
) -> None:
    result = origin_gateway.validate()

    assert result.returncode != 0
    assert ORIGIN_CERT_DIR in result.stderr


@pytest.mark.gateway
@each_site
@pytest.mark.parametrize(
    ("edge", "client"),
    [
        (CLOUDFLARE_V4, CLIENT_V4),
        (CLOUDFLARE_V4, CLIENT_V6),
        (CLOUDFLARE_V6, CLIENT_V4),
        (CLOUDFLARE_V6, CLIENT_V6),
        (LAST_INSIDE_V4, CLIENT_V4),
        (LAST_INSIDE_V6, CLIENT_V6),
        (CLOUDFLARE_V4, "2001:DB8:ABCD:12::77"),
        (CLOUDFLARE_V4, "::1"),
        (CLOUDFLARE_V4, "1:2:3:4:5:6:7:8"),
    ],
)
def test_ip_informado_pela_cloudflare_vale(
    gateway: Gateway, host: str, edge: str, client: str
) -> None:
    received = gateway.echo(host, edge, f"CF-Connecting-IP: {client}")

    assert received["xff"] == client


@pytest.mark.gateway
@each_site
def test_cadeia_do_x_forwarded_for_nao_substitui_o_ip_da_cloudflare(
    gateway: Gateway, host: str
) -> None:
    received = gateway.echo(
        host,
        CLOUDFLARE_V4,
        f"CF-Connecting-IP: {CLIENT_V4}",
        f"X-Forwarded-For: {FORGED}, {CLIENT_V4}",
    )

    assert received["xff"] == CLIENT_V4


@pytest.mark.gateway
@each_site
def test_cabecalho_da_cloudflare_nao_segue_ao_destino(gateway: Gateway, host: str) -> None:
    received = gateway.echo(host, CLOUDFLARE_V4, f"CF-Connecting-IP: {CLIENT_V4}")

    assert received["cf"] == ""


@pytest.mark.gateway
@each_site
@pytest.mark.parametrize(
    "source", [DIRECT_V4, DIRECT_V6, JUST_OUTSIDE_V4, JUST_OUTSIDE_V6, RETIRED_VPS]
)
@pytest.mark.parametrize("header", ["CF-Connecting-IP", "X-Forwarded-For"])
def test_ip_forjado_de_fora_da_faixa_e_ignorado(
    gateway: Gateway, host: str, source: str, header: str
) -> None:
    received = gateway.echo(host, source, f"{header}: {FORGED}")

    assert received == {"xff": source, "cf": ""}


@pytest.mark.gateway
def test_x_forwarded_for_vindo_da_vps_aposentada_nao_vale_no_stillon(gateway: Gateway) -> None:
    received = gateway.echo(STILLON, RETIRED_VPS, f"X-Forwarded-For: {FORGED}")

    assert received["xff"] == RETIRED_VPS


@pytest.mark.gateway
@each_site
@pytest.mark.parametrize(
    "headers",
    [
        pytest.param((), id="sem-cabecalho"),
        pytest.param((f"X-Forwarded-For: {FORGED}",), id="so-x-forwarded-for"),
        pytest.param(("CF-Connecting-IP: <script>",), id="valor-que-nao-e-ip"),
        pytest.param((f"CF-Connecting-IP: {FORGED}, {CLIENT_V4}",), id="lista-de-ips"),
        pytest.param(
            (f"CF-Connecting-IP: {CLIENT_V4}", f"CF-Connecting-IP: {FORGED}"),
            id="dois-cabecalhos",
        ),
        *[pytest.param((f"CF-Connecting-IP: {value}",), id=value) for value in NOT_AN_ADDRESS],
    ],
)
def test_borda_da_cloudflare_sem_ip_utilizavel_vale_como_quem_conectou(
    gateway: Gateway, host: str, headers: tuple[str, ...]
) -> None:
    received = gateway.echo(host, CLOUDFLARE_V4, *headers)

    assert received == {"xff": CLOUDFLARE_V4, "cf": ""}


@pytest.mark.gateway
def test_www_do_stillon_redireciona_para_o_dominio(gateway: Gateway) -> None:
    response = gateway.request(f"www.{STILLON}", DIRECT_V4)

    assert response.startswith("HTTP/1.1 308 ")
    assert "location: https://stillon.tech/\r\n" in response.lower()


@pytest.mark.gateway
def test_healthcheck_do_json_passa_no_gateway_real(gateway: Gateway) -> None:
    kind, *command = _gateway_proxy()["healthcheck"]["test"]

    assert kind == "CMD"
    _docker("exec", gateway.container, *command)
