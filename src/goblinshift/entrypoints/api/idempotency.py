"""`Idempotency-Key` na borda HTTP, igual em `POST /runs` e `POST /uploads` (1.6; #442, 57a).

O header é lido à mão e publicado com `idempotency_key_parameter`: declarado como `str | None` na
assinatura da rota, sairia na spec como `anyOf [string, null]`, e o cliente gerado mandaria o
texto "null". O formato é o de `runs.acceptance`, o mesmo que o banco confere.
"""

from typing import Any

from goblinshift.entrypoints.api.problems import ApiProblem, ProblemCode
from goblinshift.runs.acceptance import IDEMPOTENCY_KEY_PATTERN

IDEMPOTENCY_KEY_HEADER = "Idempotency-Key"
REPLAYED_HEADER = "Idempotency-Replayed"
# A requisição original costuma terminar em milissegundos; um segundo basta para o replay.
IN_PROGRESS_RETRY_AFTER_SECONDS = 1


def idempotency_key_parameter(*, required: bool, description: str | None = None) -> dict[str, Any]:
    described = {} if description is None else {"description": description}
    return {
        "name": IDEMPOTENCY_KEY_HEADER,
        "in": "header",
        "required": required,
        **described,
        "schema": {"type": "string", "pattern": f"^{IDEMPOTENCY_KEY_PATTERN.pattern}$"},
    }


def key_invalid_problem() -> ApiProblem:
    return ApiProblem(
        ProblemCode.IDEMPOTENCY_KEY_INVALID,
        "Envie Idempotency-Key com 16 a 64 caracteres [A-Za-z0-9_-].",
    )


def in_progress_problem() -> ApiProblem:
    return ApiProblem(
        ProblemCode.IDEMPOTENCY_IN_PROGRESS,
        "A requisição original com esta Idempotency-Key ainda está em andamento.",
        headers={"Retry-After": str(IN_PROGRESS_RETRY_AFTER_SECONDS)},
    )
