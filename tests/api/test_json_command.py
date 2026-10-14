"""Decodificação protegida dos comandos JSON (docs/GoblinShiftPlan.md, 1.7 e 3.6).

As rotas `/api/v1/_test/echo` (modelo Pydantic estrito) e `/api/v1/_test/raw` (JSON
decodificado) mostram o uso das duas formas da dependência.
"""

import json
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from goblinshift.entrypoints.api.json_command import (
    MAX_COMMAND_BYTES,
    MAX_COMMAND_DEPTH,
    decode_json_command,
)
from goblinshift.entrypoints.api.problems import ApiProblem, ProblemCode
from tests.api.support import assert_problem

ECHO = "/api/v1/_test/echo"
RAW = "/api/v1/_test/raw"
JSON_HEADERS = {"Content-Type": "application/json"}


def echo_body_of_size(size: int) -> bytes:
    """`{"text": "aaa..."}` com exatamente `size` bytes."""
    envelope = len(b'{"text": ""}')
    return b'{"text": "' + b"a" * (size - envelope) + b'"}'


def in_chunks(body: bytes, chunk_size: int = 4096) -> Iterator[bytes]:
    for start in range(0, len(body), chunk_size):
        yield body[start : start + chunk_size]


def nested_arrays(depth: int) -> bytes:
    return b"[" * depth + b"]" * depth


def post_raw(client: TestClient, body: bytes) -> dict[str, object]:
    response = client.post(RAW, content=body, headers=JSON_HEADERS)
    assert response.status_code == 200, response.text
    result: dict[str, object] = response.json()
    return result


def test_comando_valido_chega_a_rota_ja_validado(offline_client: TestClient) -> None:
    response = offline_client.post(ECHO, json={"text": "olá"})

    assert response.status_code == 200
    assert response.json() == {"text_length": 3}


def test_comando_com_exatamente_64_kib_e_aceito(offline_client: TestClient) -> None:
    body = echo_body_of_size(MAX_COMMAND_BYTES)

    response = offline_client.post(ECHO, content=body, headers=JSON_HEADERS)

    assert response.status_code == 200
    assert response.json() == {"text_length": MAX_COMMAND_BYTES - 12}


def test_comando_com_64_kib_mais_um_em_streaming_sem_content_length_responde_413(
    offline_client: TestClient,
) -> None:
    body = echo_body_of_size(MAX_COMMAND_BYTES + 1)
    request = offline_client.build_request(
        "POST", ECHO, content=in_chunks(body), headers=JSON_HEADERS
    )
    assert "content-length" not in request.headers

    response = offline_client.send(request)

    assert_problem(response, 413, "BODY_TOO_LARGE")


def test_comando_acima_do_limite_com_content_length_responde_413(
    offline_client: TestClient,
) -> None:
    body = echo_body_of_size(MAX_COMMAND_BYTES + 1)

    response = offline_client.post(ECHO, content=body, headers=JSON_HEADERS)

    assert_problem(response, 413, "BODY_TOO_LARGE")


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(b'{"text": "a", "text": "b"}', id="no-topo"),
        pytest.param(b'{"x": [{"k": 1, "k": 1}]}', id="aninhada"),
    ],
)
def test_chave_duplicada_responde_400(offline_client: TestClient, body: bytes) -> None:
    response = offline_client.post(RAW, content=body, headers=JSON_HEADERS)

    body_json = assert_problem(response, 400, "JSON_INVALID")
    assert "repetidas" in body_json["detail"]


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(b'{"text": "a\\u0000b"}', id="valor"),
        pytest.param(b'{"x": [{"a\\u0000": 1}]}', id="chave-aninhada"),
        pytest.param(b'{"x": ["ok", ["\\u0000"]]}', id="lista-aninhada"),
    ],
)
def test_nul_em_texto_responde_400(offline_client: TestClient, body: bytes) -> None:
    # Achado do Schemathesis: o PostgreSQL não guarda NUL em text nem em jsonb, e um nome de coluna
    # com \u0000 nos parâmetros de POST /runs virava 500 ao gravar a Run.
    response = offline_client.post(RAW, content=body, headers=JSON_HEADERS)

    body_json = assert_problem(response, 400, "JSON_INVALID")
    assert "NUL" in body_json["detail"]


@pytest.mark.parametrize("literal", [b"NaN", b"Infinity", b"-Infinity", b"1e400", b"-1e400"])
def test_numero_nao_finito_responde_400(offline_client: TestClient, literal: bytes) -> None:
    response = offline_client.post(RAW, content=b'{"x": ' + literal + b"}", headers=JSON_HEADERS)

    assert_problem(response, 400, "JSON_INVALID")


def test_profundidade_16_e_aceita(offline_client: TestClient) -> None:
    result = post_raw(offline_client, nested_arrays(MAX_COMMAND_DEPTH))

    assert result["value"] == json.loads(nested_arrays(MAX_COMMAND_DEPTH))


def test_profundidade_17_responde_400(offline_client: TestClient) -> None:
    response = offline_client.post(
        RAW, content=nested_arrays(MAX_COMMAND_DEPTH + 1), headers=JSON_HEADERS
    )

    body = assert_problem(response, 400, "JSON_INVALID")
    assert "profundidade" in body["detail"]


def test_profundidade_conta_objetos_e_arrays_misturados(offline_client: TestClient) -> None:
    sixteen = b'{"a": ' * 8 + nested_arrays(8) + b"}" * 8
    seventeen = b'{"a": ' * 9 + nested_arrays(8) + b"}" * 9

    post_raw(offline_client, sixteen)
    response = offline_client.post(RAW, content=seventeen, headers=JSON_HEADERS)

    assert_problem(response, 400, "JSON_INVALID")


def test_aninhamento_extremo_dentro_do_limite_de_bytes_responde_400_e_nao_500(
    offline_client: TestClient,
) -> None:
    depth = MAX_COMMAND_BYTES // 2

    response = offline_client.post(RAW, content=nested_arrays(depth), headers=JSON_HEADERS)

    assert_problem(response, 400, "JSON_INVALID")


def test_escalar_no_topo_tem_profundidade_zero(offline_client: TestClient) -> None:
    assert post_raw(offline_client, b'"so um texto"') == {"value": "so um texto"}


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(b"", id="vazio"),
        pytest.param(b'{"text": ', id="truncado"),
        pytest.param(b'{"text": "\xff"}', id="utf8-invalido"),
        pytest.param(b'\xef\xbb\xbf{"text": "a"}', id="bom"),
        pytest.param(b'{"x": ' + b"9" * 5000 + b"}", id="inteiro-gigante"),
    ],
)
def test_corpo_que_nao_e_json_valido_responde_400(offline_client: TestClient, body: bytes) -> None:
    response = offline_client.post(RAW, content=body, headers=JSON_HEADERS)

    assert_problem(response, 400, "JSON_INVALID")


@pytest.mark.parametrize(
    "headers",
    [
        pytest.param({}, id="sem-content-type"),
        pytest.param({"Content-Type": "text/plain"}, id="texto"),
        pytest.param({"Content-Type": "application/x-www-form-urlencoded"}, id="formulario"),
        pytest.param({"Content-Type": "application/jsonx"}, id="parecido"),
    ],
)
def test_content_type_diferente_de_json_responde_415(
    offline_client: TestClient, headers: dict[str, str]
) -> None:
    response = offline_client.post(ECHO, content=b'{"text": "a"}', headers=headers)

    assert_problem(response, 415, "UNSUPPORTED_MEDIA_TYPE")


@pytest.mark.parametrize("content_type", ["application/json; charset=utf-8", "Application/JSON"])
def test_content_type_json_com_parametro_ou_maiusculas_e_aceito(
    offline_client: TestClient, content_type: str
) -> None:
    response = offline_client.post(
        ECHO, content=b'{"text": "a"}', headers={"Content-Type": content_type}
    )

    assert response.status_code == 200


def test_campo_desconhecido_responde_422_com_o_campo(offline_client: TestClient) -> None:
    response = offline_client.post(ECHO, json={"text": "a", "status": "done"})

    body = assert_problem(response, 422, "VALIDATION_FAILED")
    assert [(e["field"], e["code"]) for e in body["errors"]] == [("body.status", "extra_forbidden")]


def test_validacao_estrita_nao_converte_numero_em_texto(offline_client: TestClient) -> None:
    response = offline_client.post(ECHO, json={"text": 123})

    body = assert_problem(response, 422, "VALIDATION_FAILED")
    assert [(e["field"], e["code"]) for e in body["errors"]] == [("body.text", "string_type")]


def test_erro_de_validacao_nao_ecoa_o_valor_enviado(offline_client: TestClient) -> None:
    response = offline_client.post(ECHO, json={"text": ["CANARIO-VALOR"]})

    assert_problem(response, 422, "VALIDATION_FAILED")
    assert "CANARIO-VALOR" not in response.text


def test_aninhamento_que_estoura_a_pilha_do_decodificador_vira_json_invalido() -> None:
    # Acima do limite de bytes, então só alcançável chamando o decodificador direto: a
    # defesa não pode depender do tamanho da pilha da thread que atende a requisição.
    depth = 200_000

    with pytest.raises(ApiProblem) as raised:
        decode_json_command(nested_arrays(depth))

    assert raised.value.code is ProblemCode.JSON_INVALID
