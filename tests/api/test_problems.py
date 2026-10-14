"""Toda resposta de erro é problem+json (docs/GoblinShiftPlan.md, 1.6 e 2.3)."""

import logging

import pytest
from fastapi.testclient import TestClient

from goblinshift.entrypoints.api.problems import (
    PROBLEMS,
    ApiProblem,
    ProblemCode,
    code_for_http_status,
)
from tests.api.support import StreamInterruptedError, assert_problem


def test_todo_codigo_do_catalogo_tem_status_e_titulo() -> None:
    assert set(PROBLEMS) == set(ProblemCode)


def test_status_do_problema_vem_do_catalogo_pelo_codigo() -> None:
    assert ApiProblem(ProblemCode.BODY_TOO_LARGE, "x").status == 413
    assert ApiProblem(ProblemCode.QUEUE_FULL, "x").status == 503


def test_tipo_e_uri_estavel_em_kebab_case() -> None:
    assert (
        ProblemCode.IDEMPOTENCY_KEY_INVALID.type_uri
        == "https://goblinshift.dev/problems/idempotency-key-invalid"
    )


def test_rota_inexistente_responde_404_em_problem_json(offline_client: TestClient) -> None:
    response = offline_client.get("/api/v1/nao-existe?token=abc")

    body = assert_problem(response, 404, "NOT_FOUND")
    assert body["instance"] == "/api/v1/nao-existe"
    assert body["title"] == "Recurso não encontrado"


def test_metodo_nao_permitido_responde_405_com_allow(offline_client: TestClient) -> None:
    response = offline_client.post("/livez")

    assert_problem(response, 405, "METHOD_NOT_ALLOWED")
    assert response.headers["allow"] == "GET"


def test_parametro_invalido_responde_422_com_erros_por_campo(
    offline_client: TestClient,
) -> None:
    response = offline_client.get("/api/v1/_test/items", params={"limit": "muitos"})

    body = assert_problem(response, 422, "VALIDATION_FAILED")
    assert body["errors"] == [
        {
            "field": "query.limit",
            "code": "int_parsing",
            "message": "Input should be a valid integer, unable to parse string as an integer",
        }
    ]


# Status que o roteador, o Starlette e o FastAPI emitem sozinhos, com o código genérico esperado.
FRAMEWORK_STATUSES = [
    (400, "BAD_REQUEST"),
    (401, "AUTHENTICATION_REQUIRED"),
    (403, "FORBIDDEN"),
    (404, "NOT_FOUND"),
    (405, "METHOD_NOT_ALLOWED"),
    (408, "REQUEST_TIMEOUT"),
    (409, "STATE_CONFLICT"),
    (410, "GONE"),
    (413, "BODY_TOO_LARGE"),
    (415, "UNSUPPORTED_MEDIA_TYPE"),
    (422, "VALIDATION_FAILED"),
    (429, "RATE_LIMITED"),
    (500, "INTERNAL_ERROR"),
    (503, "SERVICE_UNAVAILABLE"),
]
# Status sem código próprio: caem no genérico da classe (4xx -> 400, 5xx -> 500), para que o
# par (status, code) continue igual ao do catálogo.
UNCATALOGED_STATUSES = [
    (406, 400, "BAD_REQUEST"),
    (418, 400, "BAD_REQUEST"),
    (502, 500, "INTERNAL_ERROR"),
]


@pytest.mark.parametrize(("status", "code"), FRAMEWORK_STATUSES)
def test_status_emitido_pelo_framework_sai_com_o_codigo_e_o_status_do_catalogo(
    offline_client: TestClient, status: int, code: str
) -> None:
    response = offline_client.get(f"/api/v1/_test/http-error/{status}")

    assert_problem(response, status, code)
    assert response.headers["retry-after"] == "7"


@pytest.mark.parametrize(("status", "expected_status", "code"), UNCATALOGED_STATUSES)
def test_status_sem_codigo_proprio_cai_no_generico_da_classe(
    offline_client: TestClient, status: int, expected_status: int, code: str
) -> None:
    response = offline_client.get(f"/api/v1/_test/http-error/{status}")

    assert_problem(response, expected_status, code)


@pytest.mark.parametrize("status", range(400, 600))
def test_todo_status_de_erro_mapeia_para_um_codigo_com_o_mesmo_status_ou_o_generico(
    status: int,
) -> None:
    code = code_for_http_status(status)

    expected = status if status in dict(FRAMEWORK_STATUSES) else (400 if status < 500 else 500)
    assert PROBLEMS[code].status == expected


def test_erro_inesperado_responde_500_sem_vazar_detalhes(
    offline_client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.ERROR)

    response = offline_client.get("/api/v1/_test/boom")

    body = assert_problem(response, 500, "INTERNAL_ERROR")
    for leak in ("Traceback", "sqlalchemy", "ProgrammingError", "SELECT", "api_key", "conexão"):
        assert leak not in response.text
    assert body["request_id"] in body["detail"]
    # O detalhe que o cliente não vê fica no log, ligado ao request_id.
    [record] = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert body["request_id"] in record.getMessage()
    assert record.exc_info is not None


def test_request_id_e_novo_a_cada_requisicao_e_ignora_o_do_cliente(
    offline_client: TestClient,
) -> None:
    forged = "00000000-0000-0000-0000-000000000000"

    first = offline_client.get("/livez", headers={"X-Request-ID": forged})
    second = offline_client.get("/livez")

    assert first.headers["x-request-id"] != forged
    assert first.headers["x-request-id"] != second.headers["x-request-id"]


def test_rota_autenticada_sem_credencial_responde_401(offline_client: TestClient) -> None:
    response = offline_client.get("/api/v1/me")

    assert_problem(response, 401, "AUTHENTICATION_REQUIRED")
    assert response.headers["www-authenticate"] == "Bearer"


def test_erro_depois_de_a_resposta_comecar_corta_a_conexao_em_vez_de_mandar_outra(
    offline_client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.ERROR)

    # Com o status já enviado não há problem+json possível: a exceção segue para o servidor,
    # que corta a conexão, e o TestClient a devolve ao teste.
    with pytest.raises(StreamInterruptedError):
        offline_client.get("/api/v1/_test/stream-boom")

    assert any(record.exc_info for record in caplog.records)
