"""IP real do cliente e `limit_req` em `/auth/*` no Nginx real (docs/GoblinShiftPlan.md, 3.8).

Cada `GET /auth/login/{provider}` grava uma linha em `login_state` sem autenticação, então o
Nginx limita `/auth/*` por cliente: 10 por minuto, com rajada de 10. O cliente é o IP que a borda
informa no X-Forwarded-For; de quem não é a borda, o cabeçalho é ignorado, para ninguém escapar do
limite (nem forjar o IP da auditoria) trocando o valor a cada pedido. A API falsa ecoa, em
`API_ECHO`, o X-Forwarded-For que recebeu.
"""

import json
from collections.abc import Iterator

import pytest

from tests.test_nginx_logs import API_ECHO, NginxLogs

pytestmark = pytest.mark.nginx

# 10 por minuto com rajada de 10: o primeiro pedido e os 10 da rajada passam.
AUTH_ALLOWED_IN_A_ROW = 11
HTTP_NO_CONTENT = 204
HTTP_TOO_MANY_REQUESTS = 429
# Um pedido a cada 6 s repõe a cota (10 por minuto).
RETRY_AFTER_SECONDS = "6"


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


def test_429_de_auth_e_problem_json_com_retry_after(stack: NginxLogs) -> None:
    responses = stack.fetch_many(
        stack.edge, "/auth/login/google", AUTH_ALLOWED_IN_A_ROW + 1,
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
    assert problem["request_id"]
    assert set(problem) == {"type", "title", "status", "detail", "code", "request_id"}


def test_limite_de_auth_nao_afeta_a_api(stack: NginxLogs) -> None:
    header = "X-Forwarded-For: 198.51.100.20"
    _statuses(stack, stack.edge, "/auth/login/google", AUTH_ALLOWED_IN_A_ROW + 1, header)

    assert _statuses(stack, stack.edge, "/api/v1/me", 1, header) == [HTTP_NO_CONTENT]
