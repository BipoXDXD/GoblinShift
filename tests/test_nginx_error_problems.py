"""413 e falha de upstream em problem+json no Nginx real (issue #442).

O teto de corpo e a API fora do ar são respondidos pelo próprio Nginx, antes de a API ver o pedido.
Sem tradução, saíam como a página HTML do Nginx, e a SPA mostrava "erro inesperado". Em `/api/` e
no envio de conteúdo, o 413 vira BODY_TOO_LARGE e o 502/504 vira o 503 SERVICE_UNAVAILABLE da API,
com Retry-After e os cabeçalhos de segurança que a API põe nas respostas dela. Os corpos são
conferidos contra o OpenAPI sem Docker em tests/test_nginx_problem_bodies.py.
"""

import json
import shlex
import uuid
from collections.abc import Iterator
from dataclasses import dataclass

import pytest

from tests.test_nginx_logs import API, API_CLOSE, NginxLogs, Response

pytestmark = pytest.mark.nginx

HTTP_PAYLOAD_TOO_LARGE = 413
HTTP_SERVICE_UNAVAILABLE = 503
UPSTREAM_RETRY_AFTER_SECONDS = "5"
# Acima dos 64k de /api/ e dos 256m do envio de conteúdo.
API_LARGE_BODY_BYTES = 70_000
UPLOAD_DECLARED_BYTES = 300_000_000
UPLOAD_CONTENT = "/api/v1/uploads/qualquer/content"
INSTANCE_CANARY_QUERY = f"CANARY-{uuid.uuid4()}"
API_CSP = "default-src 'none'; frame-ancestors 'none'"


@dataclass(frozen=True)
class Problems:
    api_too_large: Response
    upload_too_large: Response
    api_upstream_closed: Response
    upload_api_gone: Response


def _curl(stack: NginxLogs, path: str, *curl_args: str, stdin: str = "") -> Response:
    """Um pedido da borda com a query canária; devolve a resposta inteira."""
    curl = shlex.join(
        ["curl", "-sS", "-i", *curl_args, f"http://nginx{path}?token={INSTANCE_CANARY_QUERY}"]
    )
    return Response.parse(stack.shell(stack.edge, f"{stdin}{curl}"))


@pytest.fixture(scope="module")
def problems(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Problems]:
    stack = NginxLogs(tmp_path_factory.mktemp("nginx-problems"))
    try:
        stack.start()
        stack.request(stack.other, f"{API}/aquecimento")
        # Com `Expect: 100-continue` o Nginx recusa pelo Content-Length, sem o curl mandar o corpo.
        api_too_large = _curl(
            stack, f"{API}/corpo-grande", "-X", "POST", "-H", "Expect: 100-continue",
            "--data-binary", "@-", stdin=f"head -c {API_LARGE_BODY_BYTES} /dev/zero | ",
        )  # fmt: skip
        upload_too_large = _curl(
            stack, UPLOAD_CONTENT, "-X", "PUT", "-H", "Expect: 100-continue",
            "-H", f"Content-Length: {UPLOAD_DECLARED_BYTES}",
        )  # fmt: skip
        api_upstream_closed = _curl(stack, API_CLOSE)
        # Sem a API na rede, o Nginx não a resolve (502) ou espera o IP antigo (504): os dois, 503.
        stack.remove_api()
        upload_api_gone = _curl(stack, UPLOAD_CONTENT, "-X", "PUT")
        yield Problems(api_too_large, upload_too_large, api_upstream_closed, upload_api_gone)
    finally:
        stack.stop()


CASES = [
    pytest.param(
        "api_too_large", HTTP_PAYLOAD_TOO_LARGE, "BODY_TOO_LARGE", f"{API}/corpo-grande",
        id="413-api",
    ),
    pytest.param(
        "upload_too_large", HTTP_PAYLOAD_TOO_LARGE, "BODY_TOO_LARGE", UPLOAD_CONTENT,
        id="413-envio",
    ),
    pytest.param(
        "api_upstream_closed", HTTP_SERVICE_UNAVAILABLE, "SERVICE_UNAVAILABLE", API_CLOSE,
        id="502-api",
    ),
    pytest.param(
        "upload_api_gone", HTTP_SERVICE_UNAVAILABLE, "SERVICE_UNAVAILABLE", UPLOAD_CONTENT,
        id="502-504-envio",
    ),
]  # fmt: skip


@pytest.mark.parametrize(("case", "status", "code", "instance"), CASES)
def test_erro_do_nginx_e_problem_json_do_catalogo(
    problems: Problems, case: str, status: int, code: str, instance: str
) -> None:
    response: Response = getattr(problems, case)

    assert response.status == status
    assert response.header_values("Content-Type") == ["application/problem+json"]
    problem = json.loads(response.body)
    assert problem["status"] == status
    assert problem["code"] == code
    assert problem["type"] == f"https://goblinshift.dev/problems/{code.lower().replace('_', '-')}"
    # Só o caminho, como na API: a query string pode carregar dados.
    assert problem["instance"] == instance
    assert INSTANCE_CANARY_QUERY not in response.body
    assert str(uuid.UUID(problem["request_id"])) == problem["request_id"]


@pytest.mark.parametrize("case", [case.values[0] for case in CASES])
def test_erro_do_nginx_traz_os_cabecalhos_de_seguranca_da_api(
    problems: Problems, case: str
) -> None:
    response: Response = getattr(problems, case)

    assert response.header_values("X-Content-Type-Options") == ["nosniff"]
    assert response.header_values("Content-Security-Policy") == [API_CSP]
    assert response.header_values("Cache-Control") == ["no-store"]
    assert response.header_values("Cross-Origin-Resource-Policy") == ["same-origin"]


@pytest.mark.parametrize("case", ["api_upstream_closed", "upload_api_gone"])
def test_503_do_upstream_traz_retry_after(problems: Problems, case: str) -> None:
    response: Response = getattr(problems, case)

    assert response.header_values("Retry-After") == [UPSTREAM_RETRY_AFTER_SECONDS]


def test_413_nao_traz_retry_after(problems: Problems) -> None:
    assert problems.api_too_large.header_values("Retry-After") == []
