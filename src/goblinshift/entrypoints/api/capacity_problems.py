"""Recusas de capacidade → problem+json com `Retry-After` (1.6, 1.7; plano da fatia 6, Frente D).

- Teto da conta → 429 `QUOTA_EXCEEDED`, com o tempo até a próxima expiração conhecida de um
  upload ou artefato da conta, nunca menos de 60 s (a manutenção ainda precisa remover).
- Teto do ambiente → 503 `SERVICE_UNAVAILABLE`, com 300 s.
- Cota diária → 429 `QUOTA_EXCEEDED`, com o tempo até a Run mais antiga sair da janela.
"""

import math
from datetime import timedelta
from uuid import UUID

from sqlalchemy.orm import Session

from goblinshift.entrypoints.api.problems import ApiProblem, ProblemCode
from goblinshift.files.retention import next_storage_release_in

STORAGE_MIN_RETRY_AFTER = timedelta(seconds=60)
ENVIRONMENT_RETRY_AFTER = timedelta(seconds=300)


def account_storage_problem(session: Session, owner_id: UUID) -> ApiProblem:
    """Lê a próxima expiração numa transação nova: a da recusa já foi desfeita."""
    with session.begin():
        wait = next_storage_release_in(session, owner_id)
    return ApiProblem(
        ProblemCode.QUOTA_EXCEEDED,
        "A conta atingiu o limite de armazenamento; espere arquivos expirarem ou exclua alguns.",
        headers=retry_after(max(wait or timedelta(0), STORAGE_MIN_RETRY_AFTER)),
    )


def environment_storage_problem() -> ApiProblem:
    return ApiProblem(
        ProblemCode.SERVICE_UNAVAILABLE,
        "O serviço está sem espaço de armazenamento no momento; tente mais tarde.",
        headers=retry_after(ENVIRONMENT_RETRY_AFTER),
    )


def daily_quota_problem(wait: timedelta) -> ApiProblem:
    return ApiProblem(
        ProblemCode.QUOTA_EXCEEDED,
        "A conta atingiu a cota diária de execuções ou de bytes de entrada.",
        headers=retry_after(wait),
    )


def retry_after(wait: timedelta) -> dict[str, str]:
    """Segundos inteiros, arredondados para cima: o cliente nunca volta antes da hora."""
    return {"Retry-After": str(max(1, math.ceil(wait.total_seconds())))}
