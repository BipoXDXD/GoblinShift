"""Recusas esperadas no teste de contrato (`scripts/contract_rules.py`).

O check `positive_data_acceptance` do Schemathesis acusa toda recusa de entrada que a spec permite.
Algumas regras do domínio não cabem em JSON Schema (soma do lote, cursor assinado, campo exigido
só sem `validate_only`). Cada uma vira uma regra estreita: operação, status, código do problema e
o campo ou a condição da requisição. Qualquer outra recusa continua reprovando o job.
"""

from typing import Any

import pytest

from scripts.contract_rules import (
    Rejection,
    is_expected_read_after_delete,
    is_expected_rejection,
    normalize_integral_floats,
)

CREATE_RUN = "POST /api/v1/runs"
LIST_RUNS = "GET /api/v1/runs"
CREATE_UPLOADS = "POST /api/v1/uploads"
PUT_CONTENT = "PUT /api/v1/uploads/{upload_id}/content"
GET_UPLOAD = "GET /api/v1/uploads/{upload_id}"


def _problem(code: str, *errors: tuple[str, str]) -> dict[str, Any]:
    body: dict[str, Any] = {"code": code}
    if errors:
        body["errors"] = [{"field": f, "code": c, "message": "x"} for f, c in errors]
    return body


def _rejection(
    operation: str,
    status: int,
    problem: dict[str, Any] | None,
    *,
    headers: dict[str, str] | None = None,
    query: dict[str, Any] | None = None,
    body: Any = None,
) -> Rejection:
    return Rejection(operation, status, problem, headers or {}, query or {}, body)


@pytest.mark.parametrize(
    "rejection",
    [
        _rejection(CREATE_RUN, 400, _problem("IDEMPOTENCY_KEY_INVALID")),
        _rejection(
            CREATE_RUN,
            422,
            _problem("VALIDATION_FAILED", ("body.input_upload_ids", "value_error")),
        ),
        _rejection(LIST_RUNS, 400, _problem("BAD_REQUEST"), query={"cursor": "abc"}),
        _rejection(CREATE_UPLOADS, 422, _problem("VALIDATION_FAILED", ("body.items", "invalid"))),
        _rejection(PUT_CONTENT, 422, _problem("VALIDATION_FAILED", ("body", "size_mismatch"))),
        _rejection(PUT_CONTENT, 413, _problem("BODY_TOO_LARGE")),
        _rejection(PUT_CONTENT, 410, _problem("GONE")),
        _rejection(CREATE_RUN, 422, _problem("INPUT_NOT_AVAILABLE")),
        _rejection(
            CREATE_RUN, 400, _problem("JSON_INVALID"), body={"parameters": {"x": ["a\x00"]}}
        ),
        _rejection(
            CREATE_UPLOADS,
            422,
            _problem("VALIDATION_FAILED", ("body.items.1.name", "invalid")),
            body={"items": [{"name": "a"}, {"name": ".."}]},
        ),
        _rejection(
            CREATE_RUN,
            422,
            _problem(
                "VALIDATION_FAILED",
                ("body.parameters", "value_error"),
                ("body.parameters.filters.0", "value_error"),
                ("body.input_upload_ids", "value_error"),
            ),
        ),
    ],
    ids=[
        "chave-ausente",
        "sem-uploads",
        "cursor-forjado",
        "lote-acima-de-512-mib",
        "conteudo-curto",
        "conteudo-longo",
        "upload-que-ja-falhou",
        "uploads-inexistentes",
        "nul-no-corpo",
        "nome-ponto-ponto",
        "regra-cruzada-dos-parametros",
    ],
)
def test_aceita_recusa_por_regra_que_o_json_schema_nao_expressa(rejection: Rejection) -> None:
    assert is_expected_rejection(rejection)


@pytest.mark.parametrize(
    "rejection",
    [
        # A chave foi enviada: se o formato bate com a spec, recusar é defeito.
        _rejection(
            CREATE_RUN,
            400,
            _problem("IDEMPOTENCY_KEY_INVALID"),
            headers={"idempotency-key": "a" * 16},
        ),
        # Erro do próprio schema (tamanho, tipo, enum) é drift, não regra de domínio.
        _rejection(
            CREATE_RUN,
            422,
            _problem(
                "VALIDATION_FAILED",
                ("body.input_upload_ids", "value_error"),
                ("body.parameters.name_prefix", "string_too_long"),
            ),
        ),
        _rejection(
            CREATE_RUN, 422, _problem("VALIDATION_FAILED", ("body.recipe_id", "value_error"))
        ),
        _rejection(PUT_CONTENT, 413, _problem("JSON_INVALID")),
        _rejection(CREATE_RUN, 400, _problem("JSON_INVALID"), body={"parameters": {"x": "a"}}),
        _rejection(
            CREATE_UPLOADS,
            422,
            _problem("VALIDATION_FAILED", ("body.items.0.name", "invalid")),
            body={"items": [{"name": "a/b"}, {"name": ".."}]},
        ),
        _rejection(CREATE_RUN, 422, _problem("VALIDATION_FAILED")),
        _rejection(LIST_RUNS, 400, _problem("BAD_REQUEST")),
        _rejection(LIST_RUNS, 422, _problem("VALIDATION_FAILED"), query={"cursor": "abc"}),
        _rejection(
            CREATE_UPLOADS, 422, _problem("VALIDATION_FAILED", ("body.items.0.name", "invalid"))
        ),
        _rejection(PUT_CONTENT, 422, _problem("VALIDATION_FAILED", ("body", "other"))),
        _rejection("GET /api/v1/me", 400, _problem("IDEMPOTENCY_KEY_INVALID")),
        _rejection(CREATE_RUN, 400, None),
    ],
    ids=[
        "chave-enviada",
        "erro-de-schema-junto",
        "value-error-fora-dos-parametros",
        "413-com-outro-codigo",
        "json-invalido-sem-nul",
        "nome-invalido-que-nao-e-ponto",
        "422-sem-errors",
        "400-sem-cursor",
        "cursor-com-outro-status",
        "nome-do-item",
        "outro-codigo-no-conteudo",
        "outra-operacao",
        "corpo-que-nao-e-problema",
    ],
)
def test_recusa_fora_das_regras_continua_falha(rejection: Rejection) -> None:
    assert not is_expected_rejection(rejection)


def test_normaliza_floats_inteiros_aninhados_para_int() -> None:
    body = {"items": [{"name": "a", "size": 17478.0}], "ratio": 0.5, "flag": True, "n": 3}

    normalized = normalize_integral_floats(body)

    assert normalized == {
        "items": [{"name": "a", "size": 17478}],
        "ratio": 0.5,
        "flag": True,
        "n": 3,
    }
    assert type(normalized["items"][0]["size"]) is int


def test_normalizacao_preserva_o_que_nao_e_json_de_objeto() -> None:
    assert normalize_integral_floats(b"\x89PNG") == b"\x89PNG"
    assert normalize_integral_floats(None) is None


def test_upload_excluido_continua_legivel_com_availability_deleted() -> None:
    # 1.6: GET mostra o estado do próprio upload, inclusive excluído; o check use_after_free do
    # Schemathesis espera 404.
    assert is_expected_read_after_delete(GET_UPLOAD, 200, {"availability": "deleted"})


@pytest.mark.parametrize(
    ("operation", "status", "body"),
    [
        (GET_UPLOAD, 200, {"availability": "available"}),
        (GET_UPLOAD, 200, None),
        (GET_UPLOAD, 202, {"availability": "deleted"}),
        ("GET /api/v1/artifacts/{artifact_id}", 200, {"availability": "deleted"}),
    ],
    ids=["ainda-disponivel", "sem-corpo", "outro-status", "outra-operacao"],
)
def test_leitura_apos_exclusao_fora_da_regra_continua_falha(
    operation: str, status: int, body: dict[str, Any] | None
) -> None:
    assert not is_expected_read_after_delete(operation, status, body)
