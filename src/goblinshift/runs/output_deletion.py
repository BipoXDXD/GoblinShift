"""Exclusão das saídas de uma execução terminal antes das 48 h (docs/GoblinShiftPlan.md, 1.6).

A mesma exclusão adiada de 3.6: os artefatos ficam indisponíveis na hora e a manutenção remove os
arquivos quando ninguém mais os usa. As entradas não mudam, inclusive quando uma saída reaproveita
o objeto de uma entrada (ADR 020). Repetir é sucesso.
"""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from goblinshift.files.artifacts import request_artifacts_deletion
from goblinshift.runs.lifecycle import TERMINAL_STATUSES, Status
from goblinshift.runs.queries import RunNotFoundError
from goblinshift.runs.tables import run, run_output


class RunNotTerminalError(Exception):
    """A execução ainda pode publicar saídas (→ 409)."""


def delete_run_outputs(session: Session, owner_id: UUID, run_id: UUID) -> None:
    """Roda na transação de quem chama; execução de outra conta é igual a inexistente.

    Estado terminal nunca muda (3.5), então ler o status sem trava basta: nenhuma publicação
    acontece depois dele.
    """
    status = session.execute(
        select(run.c.status).where(run.c.owner_id == owner_id, run.c.id == run_id)
    ).scalar_one_or_none()
    if status is None:
        raise RunNotFoundError
    if Status(status) not in TERMINAL_STATUSES:
        raise RunNotTerminalError
    artifact_ids = session.execute(
        select(run_output.c.artifact_id).where(
            run_output.c.owner_id == owner_id, run_output.c.run_id == run_id
        )
    ).scalars()
    request_artifacts_deletion(session, owner_id, list(artifact_ids))
