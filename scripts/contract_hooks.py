"""Hooks do Schemathesis no teste de contrato (SCHEMATHESIS_HOOKS em scripts/contract-test.sh).

Adaptador fino: as decisões ficam em `scripts.contract_rules`, testadas na suíte comum. Este módulo
só é importado pelo Schemathesis, que vem do grupo `contract` (fora do ambiente `dev`).
"""

from typing import Any

import schemathesis
from schemathesis.openapi.checks import MissingHeaderNotRejected, RejectedPositiveData, UseAfterFree

from scripts.contract_rules import (
    Rejection,
    is_expected_missing_header,
    is_expected_read_after_delete,
    is_expected_rejection,
    normalize_integral_floats,
)


@schemathesis.hook
def after_call(_context: Any, _case: Any, response: Any) -> None:
    # Um orçamento esgotado tornaria o CI verde sem testar a lógica atrás do limitador.
    body = _json_object(response)
    if response.status_code == 429 and body is not None and body.get("code") == "RATE_LIMITED":
        raise RuntimeError("Fuzzer atingiu o limite de requisições; a cobertura ficou bloqueada")


@schemathesis.hook
def map_body(_context: Any, body: Any) -> Any:
    return normalize_integral_floats(body)


@schemathesis.hook
def filter_failure(_context: Any, failure: Any, case: Any, response: Any) -> bool:
    """Descarta só as falhas explicadas por uma regra de `contract_rules`; o resto reprova."""
    operation = case.operation.label
    if isinstance(failure, UseAfterFree):
        return not is_expected_read_after_delete(
            operation, response.status_code, _json_object(response)
        )
    if isinstance(failure, MissingHeaderNotRejected):
        return not is_expected_missing_header(
            operation, failure.header_name, response.status_code, _json_object(response)
        )
    if isinstance(failure, RejectedPositiveData):
        rejection = Rejection(
            operation=operation,
            status=response.status_code,
            problem=_json_object(response),
            headers=dict(case.headers or {}),
            query=dict(case.query or {}),
            body=case.body,
        )
        return not is_expected_rejection(rejection)
    return True


def _json_object(response: Any) -> dict[str, Any] | None:
    try:
        body = response.json()
    except ValueError:
        return None
    return body if isinstance(body, dict) else None
