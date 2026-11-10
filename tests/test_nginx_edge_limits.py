"""`limit_req` por cliente em `/api/` e `/readyz` no Nginx real (shed load na borda da app).

Antes de autenticar, a API não tem conta para limitar: um cliente sozinho podia ocupar o pool do
banco com pedidos anônimos a `/api/` ou com sondas a `/readyz`. O Nginx recusa o excesso por
cliente com 429 e `Retry-After`, no mesmo formato problem+json da API. As taxas são por segundo,
então os pedidos de cada rajada saem juntos, num só `curl` em paralelo; um laço de pedidos
seguidos deixaria a cota se repor entre eles.
"""

import json
import shlex
from collections.abc import Iterator
from dataclasses import dataclass

import pytest

from tests.test_nginx_logs import NginxLogs

pytestmark = pytest.mark.nginx

HTTP_NO_CONTENT = 204
HTTP_TOO_MANY_REQUESTS = 429
# /api/: 20 por segundo com rajada de 40, então o primeiro pedido e os 40 da rajada passam.
API_ALLOWED_AT_ONCE = 41
# /readyz: 5 por segundo com rajada de 10.
READYZ_ALLOWED_AT_ONCE = 11
# Pedidos que a cota repõe enquanto a rajada sai: a 20 por segundo, um a cada 50 ms. A rajada leva
# bem menos de meio segundo, então passa de 10 só se o limite estiver errado.
REFILL_TOLERANCE = 10
RETRY_AFTER_SECONDS = "1"
# Separador que não aparece em cabeçalho nem no corpo problem+json; tab seria espaço para o `read`.
SEPARATOR = "|"


@dataclass(frozen=True)
class Hit:
    status: int
    retry_after: str
    content_type: str
    body: str


@pytest.fixture(scope="module")
def stack(tmp_path_factory: pytest.TempPathFactory) -> Iterator[NginxLogs]:
    nginx = NginxLogs(tmp_path_factory.mktemp("nginx-edge"))
    try:
        nginx.start()
        nginx.request(nginx.other, "/aquecimento")
        yield nginx
    finally:
        nginx.stop()


def _burst_command(path: str, count: int, client_ip: str) -> str:
    """`count` pedidos simultâneos; cada linha traz status, cabeçalhos e corpo de um pedido."""
    write_out = SEPARATOR.join(
        ["%{http_code}", "%header{retry-after}", "%header{content-type}", "%{filename_effective}"]
    )
    curl = shlex.join(
        [
            "curl", "-sS", "-Z", "--parallel-immediate", "--parallel-max", str(count),
            "-H", f"X-Forwarded-For: {client_ip}", "-o", "resp-#1", "-w", rf"{write_out}\n",
            f"http://nginx{path}?n=[1-{count}]",
        ]
    )  # fmt: skip
    # Corpo de cada resposta na mesma linha, depois do status: 204 não tem corpo.
    return (
        f'cd "$(mktemp -d)" && {curl} > status.tsv'
        f' && while IFS="{SEPARATOR}" read -r code retry type file;'
        f' do printf "%s{SEPARATOR}%s{SEPARATOR}%s{SEPARATOR}%s\\n" "$code" "$retry" "$type"'
        ' "$(cat "$file" 2>/dev/null)"; done < status.tsv'
    )


def _parse(output: str) -> list[Hit]:
    hits = []
    for line in output.splitlines():
        status, retry_after, content_type, body = line.split(SEPARATOR, 3)
        hits.append(Hit(int(status), retry_after, content_type, body))
    return hits


def _burst(stack: NginxLogs, path: str, count: int, client_ip: str) -> list[Hit]:
    command = _burst_command(path, count, client_ip)
    return _parse(stack.shell(stack.edge, command))


def _two_bursts(
    stack: NginxLogs, path: str, first: tuple[str, int], second: tuple[str, int]
) -> tuple[list[Hit], list[Hit]]:
    """Duas rajadas simultâneas de dois clientes: prova que um não gasta a cota do outro."""
    (first_ip, first_count), (second_ip, second_count) = first, second
    command = (
        f"({_burst_command(path, first_count, first_ip)}) > /tmp/primeiro.tsv &"
        f" ({_burst_command(path, second_count, second_ip)}) > /tmp/segundo.tsv;"
        " wait; cat /tmp/primeiro.tsv; echo FIM-DO-PRIMEIRO; cat /tmp/segundo.tsv"
    )
    output = stack.shell(stack.edge, command)
    first_output, _, second_output = output.partition("FIM-DO-PRIMEIRO\n")
    return _parse(first_output), _parse(second_output)


def _count(hits: list[Hit], status: int) -> int:
    return sum(hit.status == status for hit in hits)


# --- /api/ -----------------------------------------------------------------------------------


def test_api_deixa_passar_a_rajada_e_recusa_o_excesso(stack: NginxLogs) -> None:
    hits = _burst(stack, "/api/v1/me", 100, "198.51.100.60")

    passed = _count(hits, HTTP_NO_CONTENT)
    assert _count(hits, HTTP_TOO_MANY_REQUESTS) == len(hits) - passed > 0
    assert API_ALLOWED_AT_ONCE <= passed <= API_ALLOWED_AT_ONCE + REFILL_TOLERANCE


def test_429_da_api_e_problem_json_com_retry_after(stack: NginxLogs) -> None:
    hits = _burst(stack, "/api/v1/runs", 80, "198.51.100.61")

    limited = next(hit for hit in hits if hit.status == HTTP_TOO_MANY_REQUESTS)
    assert limited.retry_after == RETRY_AFTER_SECONDS
    assert limited.content_type == "application/problem+json"
    problem = json.loads(limited.body)
    assert problem["type"] == "https://goblinshift.dev/problems/rate-limited"
    assert problem["status"] == HTTP_TOO_MANY_REQUESTS
    assert problem["code"] == "RATE_LIMITED"
    assert problem["request_id"]
    assert set(problem) == {"type", "title", "status", "detail", "code", "request_id"}


def test_limite_da_api_vale_por_cliente(stack: NginxLogs) -> None:
    flooding, neighbor = _two_bursts(
        stack, "/api/v1/me", ("198.51.100.62", 100), ("198.51.100.63", API_ALLOWED_AT_ONCE)
    )

    assert _count(flooding, HTTP_TOO_MANY_REQUESTS) > 0
    assert [hit.status for hit in neighbor] == [HTTP_NO_CONTENT] * API_ALLOWED_AT_ONCE


def test_envio_de_conteudo_tambem_conta_no_limite_da_api(stack: NginxLogs) -> None:
    """A rota de conteúdo é uma location por regex, que o Nginx escolhe antes do prefixo /api/."""
    hits = _burst(stack, "/api/v1/uploads/qualquer/content", 100, "198.51.100.64")

    assert _count(hits, HTTP_TOO_MANY_REQUESTS) > 0


# --- /readyz ---------------------------------------------------------------------------------


def test_readyz_deixa_passar_a_rajada_e_recusa_o_excesso(stack: NginxLogs) -> None:
    hits = _burst(stack, "/readyz", 40, "198.51.100.70")

    passed = _count(hits, HTTP_NO_CONTENT)
    assert _count(hits, HTTP_TOO_MANY_REQUESTS) == len(hits) - passed > 0
    # A 5 por segundo, a cota repõe um pedido a cada 200 ms.
    assert READYZ_ALLOWED_AT_ONCE <= passed <= READYZ_ALLOWED_AT_ONCE + 2


def test_429_do_readyz_traz_retry_after(stack: NginxLogs) -> None:
    hits = _burst(stack, "/readyz", 30, "198.51.100.71")

    limited = next(hit for hit in hits if hit.status == HTTP_TOO_MANY_REQUESTS)
    assert limited.retry_after == RETRY_AFTER_SECONDS
    assert json.loads(limited.body)["code"] == "RATE_LIMITED"


def test_limite_do_readyz_vale_por_cliente(stack: NginxLogs) -> None:
    flooding, neighbor = _two_bursts(
        stack, "/readyz", ("198.51.100.72", 40), ("198.51.100.73", READYZ_ALLOWED_AT_ONCE)
    )

    assert _count(flooding, HTTP_TOO_MANY_REQUESTS) > 0
    assert [hit.status for hit in neighbor] == [HTTP_NO_CONTENT] * READYZ_ALLOWED_AT_ONCE


def test_limite_do_readyz_nao_gasta_a_cota_da_api(stack: NginxLogs) -> None:
    client = "198.51.100.74"
    _burst(stack, "/readyz", 40, client)

    hits = _burst(stack, "/api/v1/me", API_ALLOWED_AT_ONCE, client)

    assert [hit.status for hit in hits] == [HTTP_NO_CONTENT] * API_ALLOWED_AT_ONCE
