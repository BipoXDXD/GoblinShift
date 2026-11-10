"""Logs do Nginx sem IP completo nem query string (docs/GoblinShiftPlan.md, 1.8 e 3.7).

Sobe a imagem do Nginx com a SPA (docker/web.Dockerfile), com os templates do repositório, e dois
clientes: um no papel do proxy de borda (o único de quem o X-Forwarded-For vale) e outro fora
dela. A API é um Nginx mínimo que responde 204 (ou 404, o eco do X-Forwarded-For e uma conexão
fechada sem resposta, nos caminhos dos outros testes). As requisições vão a `/api/`, que o Nginx
encaminha a ela, e não à SPA. O log de acesso é provado com respostas da API; o log de erro, com
o 413 do teto de corpo e com os 502 de upstream (conexão fechada, porta recusada, API fora do DNS).
"""

import json
import shlex
import subprocess
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.nginx

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "docker" / "nginx" / "templates"
DOCKERFILE = ROOT / "docker" / "web.Dockerfile"
# Mesma tag do tests/test_nginx_spa.py: o segundo build da suíte sai do cache.
IMAGE = "goblinshift-web:test"
BUILD_TIMEOUT_SECONDS = 900
API = "/api/v1/_log"
EDGE_IP = "172.31.0.2"
DOCKER_TIMEOUT_SECONDS = 60
CANARY_QUERY = f"CANARY-{uuid.uuid4()}"
CANARY_IPV4 = "203.0.113.77"
CANARY_IPV6 = "2001:db8:abcd:12::77"
SPOOFED_IP = "198.51.100.23"
# A API falsa ecoa o X-Forwarded-For que recebeu (prova do IP real), tem um caminho que dá 404 e
# outro que fecha a conexão sem responder (o 444 do Nginx), para o 502 de upstream.
API_ECHO = "/api/v1/_eco"
API_CLOSE = "/api/v1/_fecha"
API_STUB = (
    "printf 'server { listen 8000;"
    f' location = {API_ECHO} {{ return 200 "$http_x_forwarded_for"; }}'
    f" location = {API_CLOSE} {{ return 444; }}"
    " location /api/v1/runs/ { return 404; }"
    " location / { return 204; } }' > /etc/nginx/conf.d/default.conf"
    " && exec nginx -g 'daemon off;'"
)


def _docker(*args: str, timeout: int = DOCKER_TIMEOUT_SECONDS) -> str:
    result = subprocess.run(  # noqa: S603 - argumentos montados pelo teste, sem shell
        ["docker", *args],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
        timeout=timeout,
    )
    return result.stdout


def _build_web_image() -> str:
    _docker(
        "build", "--quiet", "--file", str(DOCKERFILE), "--tag", IMAGE, str(ROOT),
        timeout=BUILD_TIMEOUT_SECONDS,
    )  # fmt: skip
    return IMAGE


class NginxLogs:
    """Pilha mínima: rede própria, dois clientes, API falsa e o Nginx com os templates do repo.

    A sub-rede é a que o Docker escolher, para o teste não disputar faixa com outras redes do
    host; por isso a cópia dos templates troca o IP da borda pelo do cliente que faz o papel dela.
    """

    def __init__(self, workdir: Path) -> None:
        suffix = uuid.uuid4().hex[:8]
        self.network = f"gs-test-nginx-logs-{suffix}"
        self.api = f"gs-test-api-{suffix}"
        self.nginx = f"gs-test-nginx-{suffix}"
        self.edge = f"gs-test-edge-{suffix}"
        self.other = f"gs-test-other-{suffix}"
        self.templates = workdir / "templates"
        self.image = _build_web_image()

    def start(self) -> None:
        _docker("network", "create", self.network)
        for client in (self.edge, self.other):
            _docker(
                "run", "-d", "--name", client, "--network", self.network,
                "--entrypoint", "sleep", self.image, "infinity",
            )  # fmt: skip
        _docker(
            "run", "-d", "--name", self.api, "--network", self.network,
            "--network-alias", "api", "--entrypoint", "sh", self.image, "-c", API_STUB,
        )  # fmt: skip
        self._copy_templates_with_edge_ip(self.ip(self.edge))
        _docker(
            "run", "-d", "--name", self.nginx, "--network", self.network,
            "--network-alias", "nginx", "-e", "GS_DOMAIN=_", "-e", "NGINX_ENVSUBST_FILTER=^GS_",
            "-v", f"{self.templates}:/etc/nginx/templates:ro", self.image,
        )  # fmt: skip

    def _copy_templates_with_edge_ip(self, edge_ip: str) -> None:
        self.templates.mkdir()
        replaced = 0
        for template in TEMPLATES.glob("*.template"):
            text = template.read_text()
            replaced += text.count(EDGE_IP)
            (self.templates / template.name).write_text(text.replace(EDGE_IP, edge_ip))
        assert replaced, f"IP da borda {EDGE_IP} não encontrado nos templates"

    def close_api_port(self) -> None:
        """Troca a API por um container com o mesmo nome na rede, sem nada ouvindo na 8000."""
        self.remove_api()
        _docker(
            "run", "-d", "--name", self.api, "--network", self.network,
            "--network-alias", "api", "--entrypoint", "sleep", self.image, "infinity",
        )  # fmt: skip

    def remove_api(self) -> None:
        """Tira a API da rede: o nome `api` deixa de existir no DNS do Docker."""
        _docker("rm", "-f", self.api)

    def ip(self, container: str) -> str:
        return _docker(
            "inspect", "-f", "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}", container
        ).strip()

    def request(self, client: str, path: str, *headers: str) -> None:
        header_args = [arg for header in headers for arg in ("-H", header)]
        _docker(
            "exec", client, "curl",
            "-fsS", "-o", "/dev/null", "--retry", "30", "--retry-connrefused",
            "--retry-all-errors", "--retry-max-time", "30",
            *header_args, f"http://nginx{path}",
        )  # fmt: skip

    def fetch(self, client: str, path: str, *headers: str) -> Response:
        """Um pedido de `client` ao Nginx, sem falhar em 4xx: status, cabeçalhos e corpo."""
        return self.fetch_many(client, path, 1, *headers)[-1]

    def fetch_many(self, client: str, path: str, count: int, *headers: str) -> list[Response]:
        """`count` pedidos seguidos num só `docker exec`, para caberem no mesmo segundo."""
        header_args = " ".join(f"-H {shlex.quote(header)}" for header in headers)
        url = shlex.quote(f"http://nginx{path}")
        loop = (
            f"for i in $(seq {count}); do curl -sS -i {header_args} {url};"
            f" printf '{RESPONSE_SEPARATOR}'; done"
        )
        dump = self.shell(client, loop)
        return [Response.parse(raw) for raw in dump.split(RESPONSE_SEPARATOR)[:count]]

    def shell(self, client: str, command: str) -> str:
        """Roda `command` no shell de `client` e devolve a saída padrão."""
        return _docker("exec", client, "sh", "-c", command)

    def logs(self) -> tuple[str, str]:
        result = subprocess.run(  # noqa: S603 - argumentos montados pelo teste, sem shell
            ["docker", "logs", self.nginx],  # noqa: S607
            capture_output=True,
            text=True,
            check=True,
            timeout=DOCKER_TIMEOUT_SECONDS,
        )
        return result.stdout, result.stderr

    def stop(self) -> None:
        subprocess.run(  # noqa: S603 - argumentos montados pelo teste, sem shell
            ["docker", "rm", "-f", self.nginx, self.api, self.edge, self.other],  # noqa: S607
            capture_output=True,
            check=False,
            timeout=DOCKER_TIMEOUT_SECONDS,
        )
        subprocess.run(  # noqa: S603 - argumentos montados pelo teste, sem shell
            ["docker", "network", "rm", self.network],  # noqa: S607
            capture_output=True,
            check=False,
            timeout=DOCKER_TIMEOUT_SECONDS,
        )


RESPONSE_SEPARATOR = "\n--gs-test-fim-da-resposta--\n"


@dataclass(frozen=True)
class Response:
    status: int
    headers: list[tuple[str, str]]
    body: str

    @classmethod
    def parse(cls, raw: str) -> Response:
        # O `text=True` do subprocess já trocou os `\r\n` do HTTP por `\n`.
        head, _, body = raw.lstrip().partition("\n\n")
        status_line, *header_lines = head.splitlines()
        headers = []
        for line in header_lines:
            name, _, value = line.partition(":")
            headers.append((name.strip().lower(), value.strip()))
        return cls(int(status_line.split()[1]), headers, body)

    def header_values(self, name: str) -> list[str]:
        return [value for header, value in self.headers if header == name.lower()]


@dataclass(frozen=True)
class Captured:
    stdout: str
    stderr: str
    other_ip: str


@pytest.fixture(scope="module")
def nginx_logs(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Captured]:
    stack = NginxLogs(tmp_path_factory.mktemp("nginx"))
    try:
        stack.start()
        # Aquecimento sem canário: as retentativas enquanto o Nginx sobe ficam fora das provas.
        stack.request(stack.other, f"{API}/aquecimento")
        stack.request(
            stack.edge,
            f"{API}/borda-v4?q={CANARY_QUERY}&token={CANARY_QUERY}",
            f"X-Forwarded-For: {CANARY_IPV4}",
            f"Referer: https://example.com/?q={CANARY_QUERY}",
        )
        stack.request(stack.edge, f"{API}/borda-v6", f"X-Forwarded-For: {CANARY_IPV6}")
        stack.request(stack.other, f"{API}/direto", f"X-Forwarded-For: {SPOOFED_IP}")
        stdout, stderr = stack.logs()
        yield Captured(stdout, stderr, stack.ip(stack.other))
    finally:
        stack.stop()


def _access_entries(stdout: str) -> dict[str, dict[str, Any]]:
    entries = [json.loads(line) for line in stdout.splitlines() if line.startswith("{")]
    return {entry["path"]: entry for entry in entries}


def test_ip_completo_do_cliente_nao_aparece_em_log_nenhum(nginx_logs: Captured) -> None:
    for full_ip in (CANARY_IPV4, CANARY_IPV6, SPOOFED_IP, nginx_logs.other_ip):
        assert full_ip not in nginx_logs.stdout
        assert full_ip not in nginx_logs.stderr


def test_query_string_nao_aparece_em_log_nenhum(nginx_logs: Captured) -> None:
    assert CANARY_QUERY not in nginx_logs.stdout
    assert CANARY_QUERY not in nginx_logs.stderr


def test_ipv4_vindo_da_borda_e_registrado_truncado_em_24(nginx_logs: Captured) -> None:
    entry = _access_entries(nginx_logs.stdout)[f"{API}/borda-v4"]
    assert entry["client"] == "203.0.113.0"
    assert entry["status"] == 204


def test_ipv6_vindo_da_borda_e_registrado_truncado_em_48(nginx_logs: Captured) -> None:
    assert _access_entries(nginx_logs.stdout)[f"{API}/borda-v6"]["client"] == "2001:db8:abcd::"


def test_x_forwarded_for_de_fora_da_borda_e_ignorado(nginx_logs: Captured) -> None:
    network_24 = nginx_logs.other_ip.rsplit(".", 1)[0] + ".0"
    assert _access_entries(nginx_logs.stdout)[f"{API}/direto"]["client"] == network_24


def test_log_de_acesso_traz_so_os_campos_de_diagnostico(nginx_logs: Captured) -> None:
    # Sem Referer nem User-Agent: o Referer carrega a query string da página de origem.
    assert set(_access_entries(nginx_logs.stdout)[f"{API}/borda-v4"]) == {
        "time",
        "client",
        "method",
        "path",
        "status",
        "bytes_sent",
        "request_time",
        "upstream_status",
        "upstream_response_time",
        "upstream_connect_time",
    }


# --- Log de erro: 413 e 502 sem IP nem query ---------------------------------------------------

# O log de erro do Nginx grava `client:` (IP completo) e `request:` (com a query) no 413 do teto de
# corpo e em toda falha de upstream. Ele fica só com o que é crítico (default.conf); o diagnóstico
# desses casos vem do log de acesso, que trunca o IP, descarta a query e traz o status do upstream.
ERROR_CANARY_QUERY = f"CANARY-{uuid.uuid4()}"
# Acima dos 64k de `client_max_body_size` em /api/.
LARGE_BODY_BYTES = 70_000
# Pedidos a cada meio segundo por mais tempo que o `valid=5s` do resolver: o último já sai com a
# API resolvida de novo, e não com o IP que o Nginx guardou de antes da troca.
RESOLVER_WINDOW_REQUESTS = 16
HTTP_PAYLOAD_TOO_LARGE = 413
HTTP_BAD_GATEWAY = 502


@dataclass(frozen=True)
class ErrorCase:
    path: str
    client_ip: str


LARGE_BODY = ErrorCase(f"{API}/corpo-grande", "203.0.113.81")
UPSTREAM_CLOSED = ErrorCase(API_CLOSE, "203.0.113.82")
PORT_CLOSED = ErrorCase(f"{API}/porta-fechada", "203.0.113.83")
API_GONE = ErrorCase(f"{API}/sem-api", "203.0.113.84")


@dataclass(frozen=True)
class ErrorRun:
    stdout: str
    stderr: str
    last_status: dict[ErrorCase, int]


def _canary_curl(case: ErrorCase, *curl_args: str) -> str:
    """Um pedido da borda com o IP e a query canários; imprime só o status."""
    return shlex.join(
        [
            "curl", "-sS", "-o", "/dev/null", "-w", r"%{http_code}\n",
            "-H", f"X-Forwarded-For: {case.client_ip}", *curl_args,
            f"http://nginx{case.path}?token={ERROR_CANARY_QUERY}",
        ]
    )  # fmt: skip


def _last_status(stack: NginxLogs, command: str) -> int:
    return int(stack.shell(stack.edge, command).split()[-1])


def _large_body_status(stack: NginxLogs) -> int:
    # Com `Expect: 100-continue` o Nginx recusa pelo Content-Length, sem o curl mandar o corpo.
    curl = _canary_curl(
        LARGE_BODY, "-X", "POST", "-H", "Expect: 100-continue", "--data-binary", "@-"
    )
    return _last_status(stack, f"head -c {LARGE_BODY_BYTES} /dev/zero | {curl}")


def _status_after_resolver_window(stack: NginxLogs, case: ErrorCase) -> int:
    curl = _canary_curl(case)
    return _last_status(
        stack, f"for i in $(seq {RESOLVER_WINDOW_REQUESTS}); do {curl}; sleep 0.5; done; {curl}"
    )


@pytest.fixture(scope="module")
def error_logs(tmp_path_factory: pytest.TempPathFactory) -> Iterator[ErrorRun]:
    stack = NginxLogs(tmp_path_factory.mktemp("nginx-erro"))
    try:
        stack.start()
        stack.request(stack.other, f"{API}/aquecimento")
        last_status = {
            LARGE_BODY: _large_body_status(stack),
            UPSTREAM_CLOSED: _last_status(stack, _canary_curl(UPSTREAM_CLOSED)),
        }
        stack.close_api_port()
        last_status[PORT_CLOSED] = _status_after_resolver_window(stack, PORT_CLOSED)
        stack.remove_api()
        last_status[API_GONE] = _status_after_resolver_window(stack, API_GONE)
        stdout, stderr = stack.logs()
        yield ErrorRun(stdout, stderr, last_status)
    finally:
        stack.stop()


@pytest.mark.parametrize(
    ("case", "status"),
    [
        (LARGE_BODY, HTTP_PAYLOAD_TOO_LARGE),
        (UPSTREAM_CLOSED, HTTP_BAD_GATEWAY),
        (PORT_CLOSED, HTTP_BAD_GATEWAY),
        (API_GONE, HTTP_BAD_GATEWAY),
    ],
)
def test_caminho_de_erro_responde_com_o_status_esperado(
    error_logs: ErrorRun, case: ErrorCase, status: int
) -> None:
    # Sem o erro de verdade a prova do log não diz nada: o caminho que escreveria no log rodou.
    assert error_logs.last_status[case] == status


@pytest.mark.parametrize(
    "canary",
    [
        ERROR_CANARY_QUERY,
        *(case.client_ip for case in (LARGE_BODY, UPSTREAM_CLOSED, PORT_CLOSED, API_GONE)),
    ],
)
def test_413_e_502_nao_registram_ip_nem_query(error_logs: ErrorRun, canary: str) -> None:
    assert canary not in error_logs.stdout
    assert canary not in error_logs.stderr


def test_log_de_acesso_registra_o_413_sem_upstream(error_logs: ErrorRun) -> None:
    entry = _access_entries(error_logs.stdout)[LARGE_BODY.path]
    assert entry["status"] == HTTP_PAYLOAD_TOO_LARGE
    assert entry["client"] == "203.0.113.0"
    assert entry["upstream_status"] == ""


def test_log_de_acesso_mostra_o_502_do_upstream_que_fechou_a_conexao(error_logs: ErrorRun) -> None:
    entry = _access_entries(error_logs.stdout)[UPSTREAM_CLOSED.path]
    assert entry["status"] == HTTP_BAD_GATEWAY
    assert entry["upstream_status"] == "502"
    # Conectou: o tempo de conexão diferencia este caso da porta recusada.
    assert entry["upstream_connect_time"] not in {"", "-"}


def test_log_de_acesso_mostra_o_502_da_porta_recusada(error_logs: ErrorRun) -> None:
    entry = _access_entries(error_logs.stdout)[PORT_CLOSED.path]
    assert entry["status"] == HTTP_BAD_GATEWAY
    assert entry["upstream_status"] == "502"
    assert entry["upstream_connect_time"] == "-"


def test_log_de_acesso_mostra_o_502_sem_upstream_quando_a_api_sai_do_dns(
    error_logs: ErrorRun,
) -> None:
    # Sem resolver o nome, o Nginx não contata upstream nenhum: status do upstream vazio.
    entry = _access_entries(error_logs.stdout)[API_GONE.path]
    assert entry["status"] == HTTP_BAD_GATEWAY
    assert entry["upstream_status"] == ""
