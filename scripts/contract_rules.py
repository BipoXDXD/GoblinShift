"""Recusas que o teste de contrato aceita, e só elas (scripts/contract-test.sh).

O check `positive_data_acceptance` do Schemathesis gera entradas que a spec permite e exige que a
API as aceite. As regras abaixo são do domínio e não cabem em JSON Schema; cada uma casa com uma
operação, um status, um código de problema e o campo ou a condição que a explicam. Recusa fora
delas continua reprovando o job, porque indica spec mais frouxa que a API.

Sem dependência do Schemathesis, para ser testado na suíte comum; o adaptador é
`scripts/contract_hooks.py`.
"""

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

_OK = 200
_BAD_REQUEST = 400
_GONE = 410
_CONTENT_TOO_LARGE = 413
_UNPROCESSABLE = 422
# Código do Pydantic para erro levantado por validador próprio, não por uma restrição do schema.
_DOMAIN_ERROR = "value_error"
_GET_UPLOAD = "GET /api/v1/uploads/{upload_id}"
_ITEM_NAME_FIELD = re.compile(r"body\.items\.(?P<index>\d+)\.name")


@dataclass(frozen=True)
class Rejection:
    """Resposta 4xx a uma requisição que a spec permite."""

    operation: str
    status: int
    problem: Mapping[str, Any] | None
    headers: Mapping[str, str]
    query: Mapping[str, Any]
    body: Any = None

    def has_header(self, name: str) -> bool:
        return name.lower() in {header.lower() for header in self.headers}

    def field_errors(self) -> set[tuple[str, str]]:
        errors = (self.problem or {}).get("errors") or []
        return {(error["field"], error["code"]) for error in errors}


@dataclass(frozen=True)
class _Rule:
    operation: str
    status: int
    code: str
    applies: Callable[[Rejection], bool]


def _only_errors(*allowed: tuple[str, str]) -> Callable[[Rejection], bool]:
    def applies(rejection: Rejection) -> bool:
        errors = rejection.field_errors()
        return bool(errors) and errors <= set(allowed)

    return applies


def _has_nul(value: Any) -> bool:
    if isinstance(value, str):
        return "\0" in value
    if isinstance(value, dict):
        return any(_has_nul(key) or _has_nul(item) for key, item in value.items())
    if isinstance(value, list):
        return any(_has_nul(item) for item in value)
    return False


def _only_dot_names(rejection: Rejection) -> bool:
    """Todo erro aponta um `items[i].name` "." ou "..", fora do padrão por exigir lookahead."""
    errors = rejection.field_errors()
    items = rejection.body.get("items") if isinstance(rejection.body, dict) else None
    if not errors or not isinstance(items, list):
        return False
    return all(code == "invalid" and _is_dot_name(items, field) for field, code in errors)


def _is_dot_name(items: list[Any], field: str) -> bool:
    match = _ITEM_NAME_FIELD.fullmatch(field)
    if match is None or int(match["index"]) >= len(items):
        return False
    item = items[int(match["index"])]
    return isinstance(item, dict) and item.get("name") in {".", ".."}


def _only_run_domain_errors(rejection: Rejection) -> bool:
    """Só erros de validador próprio, nos uploads citados ou nos parâmetros da receita."""
    errors = rejection.field_errors()
    return bool(errors) and all(
        code == _DOMAIN_ERROR
        and (field == "body.input_upload_ids" or field.partition(".")[2].startswith("parameters"))
        for field, code in errors
    )


_ANY_OPERATION = "*"

_RULES = (
    # O PostgreSQL não guarda NUL em text nem em jsonb, e o JSON Schema não tem como proibi-lo em
    # todo texto; o decodificador dos comandos o recusa com 400 (`json_command`).
    _Rule(_ANY_OPERATION, _BAD_REQUEST, "JSON_INVALID", lambda rejection: _has_nul(rejection.body)),
    _Rule("POST /api/v1/uploads", _UNPROCESSABLE, "VALIDATION_FAILED", _only_dot_names),
    # A chave só é exigida sem `validate_only`, e OpenAPI não tem parâmetro condicional. Enviada
    # no formato publicado, a recusa seria defeito.
    _Rule(
        "POST /api/v1/runs",
        _BAD_REQUEST,
        "IDEMPOTENCY_KEY_INVALID",
        lambda rejection: not rejection.has_header("Idempotency-Key"),
    ),
    # Uploads de 1 a `max_inputs` da versão, exceto com `validate_only` (depende da receita e da
    # query), e as regras cruzadas dos parâmetros (`name_prefix` só com `indexed`, valor do filtro
    # conforme o operador, colunas repetidas), que os validadores do catálogo aplicam.
    _Rule("POST /api/v1/runs", _UNPROCESSABLE, "VALIDATION_FAILED", _only_run_domain_errors),
    # Os ids gerados não são uploads prontos da conta; existência não é assunto de schema.
    _Rule("POST /api/v1/runs", _UNPROCESSABLE, "INPUT_NOT_AVAILABLE", lambda _rejection: True),
    # O cursor é opaco e assinado com HMAC: um texto gerado ao acaso nunca é válido.
    _Rule(
        "GET /api/v1/runs",
        _BAD_REQUEST,
        "BAD_REQUEST",
        lambda rejection: "cursor" in rejection.query,
    ),
    # A soma dos tamanhos do lote (512 MiB) não é expressável por item.
    _Rule(
        "POST /api/v1/uploads",
        _UNPROCESSABLE,
        "VALIDATION_FAILED",
        _only_errors(("body.items", "invalid")),
    ),
    # O conteúdo tem de ter o tamanho declarado na intenção, que a spec do PUT não conhece.
    _Rule(
        "PUT /api/v1/uploads/{upload_id}/content",
        _UNPROCESSABLE,
        "VALIDATION_FAILED",
        _only_errors(("body", "size_mismatch")),
    ),
    _Rule(
        "PUT /api/v1/uploads/{upload_id}/content",
        _CONTENT_TOO_LARGE,
        "BODY_TOO_LARGE",
        lambda _rejection: True,
    ),
    # Nos testes com estado, o upload já falhou (conteúdo de tamanho errado) ou foi excluído.
    _Rule("PUT /api/v1/uploads/{upload_id}/content", _GONE, "GONE", lambda _rejection: True),
)


def is_expected_rejection(rejection: Rejection) -> bool:
    if rejection.problem is None:
        return False
    code = rejection.problem.get("code")
    return any(
        rule.operation in {rejection.operation, _ANY_OPERATION}
        and rule.status == rejection.status
        and rule.code == code
        and rule.applies(rejection)
        for rule in _RULES
    )


def is_expected_read_after_delete(
    operation: str, status: int, body: Mapping[str, Any] | None
) -> bool:
    """Upload excluído continua legível pelo dono, com `availability: deleted` (1.6).

    O check `use_after_free` espera 404 depois do DELETE; aqui a exclusão é adiada (ADR 007) e o
    estado é justamente o que o GET informa. Excluído que voltasse como disponível seria defeito.
    """
    return (
        operation == _GET_UPLOAD
        and status == _OK
        and body is not None
        and body.get("availability") == "deleted"
    )


def normalize_integral_floats(body: Any) -> Any:
    """`17478.0` → `17478`, em qualquer nível do JSON.

    Para o JSON Schema, `17478.0` é `integer`; o Pydantic em modo estrito (`json_command`) o
    recusa de propósito, e clientes reais (`JSON.stringify`) nunca o escrevem. Normalizar o corpo
    gerado mantém o caso de teste útil em vez de recusá-lo pela notação do número.
    """
    if isinstance(body, dict):
        return {key: normalize_integral_floats(value) for key, value in body.items()}
    if isinstance(body, list):
        return [normalize_integral_floats(item) for item in body]
    if isinstance(body, float) and body.is_integer():
        return int(body)
    return body
