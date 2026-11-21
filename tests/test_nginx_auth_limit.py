"""IP real do cliente e `limit_req` em `/auth/*` no Nginx real (docs/GoblinShiftPlan.md, 3.8).

Cada `GET /auth/login/{provider}` grava uma linha em `login_state` sem autenticação, então o
Nginx limita `/auth/*` por cliente: 10 por minuto, com rajada de 10. O cliente é o IP que a borda
informa no X-Forwarded-For; de quem não é a borda, o cabeçalho é ignorado, para ninguém escapar do
limite (nem forjar o IP da auditoria) trocando o valor a cada pedido. Em IPv6 o cliente é o /64
(ADR 041). A API falsa ecoa, em `API_ECHO`, o X-Forwarded-For que recebeu.
"""

import json
import re
import uuid
from collections.abc import Iterator

import pytest

from tests.test_nginx_logs import API_ECHO, TEMPLATES, NginxLogs

pytestmark = pytest.mark.nginx

# 10 por minuto com rajada de 10: o primeiro pedido e os 10 da rajada passam.
AUTH_ALLOWED_IN_A_ROW = 11
HTTP_NO_CONTENT = 204
HTTP_TOO_MANY_REQUESTS = 429
# Um pedido a cada 6 s repõe a cota (10 por minuto).
RETRY_AFTER_SECONDS = "6"
AUTH_LIMIT = re.compile(r"limit_req zone=auth(?: |$)")
INSTANCE_CANARY_QUERY = f"CANARY-{uuid.uuid4()}"


@pytest.fixture(scope="module")
def stack(tmp_path_factory: pytest.TempPathFactory) -> Iterator[NginxLogs]:
    nginx = NginxLogs(tmp_path_factory.mktemp("nginx-auth"))
    try:
        nginx.start()
        nginx.request(nginx.other, "/aquecimento")
        yield nginx
    finally:
        nginx.stop()


def _statuses(stack: NginxLogs, client: str, path: str, count: int, *headers: str) -> list[int]:
    return [response.status for response in stack.fetch_many(client, path, count, *headers)]


# --- IP real ----------------------------------------------------------------------------------


def test_api_recebe_o_ip_do_cliente_informado_pela_borda(stack: NginxLogs) -> None:
    response = stack.fetch(stack.edge, API_ECHO, "X-Forwarded-For: 203.0.113.7")

    assert response.body == "203.0.113.7"


def test_api_recebe_so_o_ip_do_cliente_e_nao_a_cadeia(stack: NginxLogs) -> None:
    """A borda troca o cabeçalho pelo IP de quem conectou nela; o Nginx repassa um IP só."""
    response = stack.fetch(stack.edge, API_ECHO, "X-Forwarded-For: 192.0.2.50, 203.0.113.8")

    assert response.body == "203.0.113.8"


def test_api_recebe_o_ipv6_do_cliente_informado_pela_borda(stack: NginxLogs) -> None:
    response = stack.fetch(stack.edge, API_ECHO, "X-Forwarded-For: 2001:db8:abcd:12::77")

    assert response.body == "2001:db8:abcd:12::77"


def test_ip_informado_por_quem_nao_e_a_borda_e_ignorado(stack: NginxLogs) -> None:
    response = stack.fetch(stack.other, API_ECHO, "X-Forwarded-For: 203.0.113.9")

    assert response.body == stack.ip(stack.other)


# --- limit_req de /auth -----------------------------------------------------------------------


def test_limite_de_auth_vale_por_cliente_real(stack: NginxLogs) -> None:
    first = _statuses(
        stack, stack.edge, "/auth/login/google", AUTH_ALLOWED_IN_A_ROW + 2,
        "X-Forwarded-For: 198.51.100.1",
    )  # fmt: skip
    other_client = _statuses(
        stack, stack.edge, "/auth/login/google", 1, "X-Forwarded-For: 198.51.100.2"
    )

    assert first == [HTTP_NO_CONTENT] * AUTH_ALLOWED_IN_A_ROW + [HTTP_TOO_MANY_REQUESTS] * 2
    assert other_client == [HTTP_NO_CONTENT]


def test_limite_vale_para_todo_o_prefixo_auth(stack: NginxLogs) -> None:
    header = "X-Forwarded-For: 198.51.100.3"
    _statuses(stack, stack.edge, "/auth/login/google", AUTH_ALLOWED_IN_A_ROW, header)

    assert _statuses(stack, stack.edge, "/auth/callback/github", 1, header) == [
        HTTP_TOO_MANY_REQUESTS
    ]


def test_quem_nao_e_a_borda_nao_escapa_do_limite_trocando_o_ip_informado(
    stack: NginxLogs,
) -> None:
    path = "/auth/login/google"
    used_up = _statuses(
        stack, stack.other, path, AUTH_ALLOWED_IN_A_ROW, "X-Forwarded-For: 192.0.2.1"
    )
    spoofed = _statuses(stack, stack.other, path, 1, "X-Forwarded-For: 192.0.2.200")

    assert used_up == [HTTP_NO_CONTENT] * AUTH_ALLOWED_IN_A_ROW
    assert spoofed == [HTTP_TOO_MANY_REQUESTS]


def _used_up_then(stack: NginxLogs, first: str, second: str) -> int:
    """Esgota a cota de `first` e devolve o status de um pedido de `second`."""
    path = "/auth/login/google"
    used_up = _statuses(stack, stack.edge, path, AUTH_ALLOWED_IN_A_ROW, f"X-Forwarded-For: {first}")
    assert used_up == [HTTP_NO_CONTENT] * AUTH_ALLOWED_IN_A_ROW
    return _statuses(stack, stack.edge, path, 1, f"X-Forwarded-For: {second}")[0]


@pytest.mark.parametrize(
    ("first", "second"),
    [
        pytest.param(
            "2001:db8:a:1::", "2001:db8:a:1:ffff:ffff:ffff:ffff", id="primeiro-e-ultimo-do-64"
        ),
        pytest.param("2001:0DB8:000A:0002::1", "2001:db8:a:2:0:0:0:2", id="grafias-diferentes"),
        pytest.param("2001:db8:b::1", "2001:db8:b:0:1:2:3:4", id="prefixo-com-grupo-zero"),
        pytest.param("2001:db8::1", "2001:db8::ffff:0:0:2", id="prefixo-com-dois-grupos-zero"),
        pytest.param("2001::1", "2001::5:6:7:8", id="prefixo-curto"),
        pytest.param("::2", "::3", id="so-zeros-no-prefixo"),
    ],
)
def test_enderecos_ipv6_do_mesmo_64_dividem_o_limite(
    stack: NginxLogs, first: str, second: str
) -> None:
    assert _used_up_then(stack, first, second) == HTTP_TOO_MANY_REQUESTS


@pytest.mark.parametrize(
    ("first", "second"),
    [
        pytest.param("2001:db8:c:10::1", "2001:db8:c:11::1", id="64-vizinhos"),
        pytest.param("2001:db8:d::1", "2001:db8:d:1::1", id="grupo-zero-e-vizinho"),
        pytest.param("2001:0:0:1:2:3:4:5", "2001:0:0:2:2:3:4:5", id="compressao-antes-do-limite"),
        pytest.param("::e:1:2:3:4:5", "::e:2:2:3:4:5", id="prefixo-comprimido-no-inicio"),
    ],
)
def test_prefixos_ipv6_diferentes_nao_dividem_o_limite(
    stack: NginxLogs, first: str, second: str
) -> None:
    assert _used_up_then(stack, first, second) == HTTP_NO_CONTENT


@pytest.mark.parametrize(
    ("first", "second"),
    [
        pytest.param("198.51.100.30", "::ffff:198.51.100.30", id="ipv4-e-mapeado"),
        pytest.param("::ffff:c633:641f", "198.51.100.31", id="mapeado-em-hexadecimal-e-ipv4"),
        pytest.param("::ffff:8.8.10.4", "8.8.10.4", id="mapeado-com-byte-0a-e-ipv4"),
    ],
)
def test_ipv4_mapeado_em_ipv6_conta_como_o_proprio_ipv4(
    stack: NginxLogs, first: str, second: str
) -> None:
    assert _used_up_then(stack, first, second) == HTTP_TOO_MANY_REQUESTS


def test_ipv4_mapeados_diferentes_nao_dividem_o_limite(stack: NginxLogs) -> None:
    status = _used_up_then(stack, "::ffff:198.51.100.40", "::ffff:198.51.100.41")

    assert status == HTTP_NO_CONTENT


def test_ipv4_conta_por_endereco_e_nao_por_rede(stack: NginxLogs) -> None:
    assert _used_up_then(stack, "198.51.100.50", "198.51.100.51") == HTTP_NO_CONTENT


def test_429_de_auth_e_problem_json_com_retry_after(stack: NginxLogs) -> None:
    responses = stack.fetch_many(
        stack.edge, f"/auth/login/google?state={INSTANCE_CANARY_QUERY}", AUTH_ALLOWED_IN_A_ROW + 1,
        "X-Forwarded-For: 198.51.100.9",
    )  # fmt: skip

    limited = responses[-1]
    assert limited.status == HTTP_TOO_MANY_REQUESTS
    assert limited.header_values("Content-Type") == ["application/problem+json"]
    assert limited.header_values("Retry-After") == [RETRY_AFTER_SECONDS]
    problem = json.loads(limited.body)
    assert problem["type"] == "https://goblinshift.dev/problems/rate-limited"
    assert problem["status"] == HTTP_TOO_MANY_REQUESTS
    assert problem["code"] == "RATE_LIMITED"
    # Só o caminho, como na API: a query string pode carregar dados.
    assert problem["instance"] == "/auth/login/google"
    assert INSTANCE_CANARY_QUERY not in limited.body
    assert str(uuid.UUID(problem["request_id"])) == problem["request_id"]
    assert set(problem) == {"type", "title", "status", "detail", "instance", "code", "request_id"}


def test_429_de_auth_traz_os_cabecalhos_de_seguranca_da_api(stack: NginxLogs) -> None:
    responses = stack.fetch_many(
        stack.edge, "/auth/login/google", AUTH_ALLOWED_IN_A_ROW + 1,
        "X-Forwarded-For: 198.51.100.10",
    )  # fmt: skip

    limited = responses[-1]
    assert limited.status == HTTP_TOO_MANY_REQUESTS
    assert limited.header_values("X-Content-Type-Options") == ["nosniff"]
    assert limited.header_values("Content-Security-Policy") == [
        "default-src 'none'; frame-ancestors 'none'"
    ]
    assert limited.header_values("Cache-Control") == ["no-store"]


def test_limite_de_auth_nao_afeta_a_api(stack: NginxLogs) -> None:
    header = "X-Forwarded-For: 198.51.100.20"
    _statuses(stack, stack.edge, "/auth/login/google", AUTH_ALLOWED_IN_A_ROW + 1, header)

    assert _statuses(stack, stack.edge, "/api/v1/me", 1, header) == [HTTP_NO_CONTENT]


# --- configuração, sem Docker -----------------------------------------------------------------


def _location_directives(config: str) -> dict[str, list[str]]:
    """Diretivas simples de cada `location`, pelo cabeçalho normalizado (`location /auth/`).

    Lê o arquivo caractere a caractere, como o Nginx: `;` fecha a diretiva, `{` e `}` abrem e
    fecham bloco, `#` fora de aspas comenta até o fim da linha e o texto entre aspas (o JSON dos
    `return 429`) não conta. Não depende de quebras de linha nem de onde ficam as chaves.
    """
    blocks: dict[str, list[str]] = {}
    stack: list[str] = []
    buffer: list[str] = []
    quote: str | None = None
    comment = False
    for char in config:
        if comment:
            comment = char != "\n"
        elif quote is not None:
            buffer.append(char)
            quote = None if char == quote else quote
        elif char in "'\"":
            buffer.append(char)
            quote = char
        elif char == "#":
            comment = True
        elif char in "{;}":
            statement = " ".join("".join(buffer).split())
            buffer.clear()
            if char == "{":
                stack.append(statement)
                blocks.setdefault(statement, [])
            elif char == ";" and stack:
                blocks[stack[-1]].append(statement)
            elif char == "}":
                stack.pop()
        else:
            buffer.append(char)
    return {name: body for name, body in blocks.items() if name.startswith("location ")}


def test_location_de_auth_aplica_o_limite_da_zona_auth() -> None:
    """Sem Docker: o login anônimo só tem o limite do proxy, já que a API conta por conta."""
    locations = _location_directives((TEMPLATES / "default.conf.template").read_text())

    limits = [line for line in locations["location /auth/"] if line.startswith("limit_req ")]

    assert len(limits) == 1
    assert AUTH_LIMIT.match(limits[0])
