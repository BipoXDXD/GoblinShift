"""Cancelamento pedido pelo dono (docs/GoblinShiftPlan.md, 3.5).

O pedido persiste a intenção e decide pela tabela de transições:
- na fila, ou esperando retentativa (nenhum executor vivo): vira `canceled` na hora e libera os
  usos numa transação seguinte;
- com executor possivelmente vivo: grava `cancel_requested_at` e `stopping`; o coordenador (ou a
  manutenção, se o worker sumiu) para o executor e só então confirma `canceled`;
- já terminal: nada muda, e quem chama devolve o estado real. Se o sucesso foi confirmado
  primeiro, permanece sucesso.

Publicação e cancelamento disputam o mesmo registro da Run (`FOR UPDATE`): o primeiro a
confirmar decide.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session, sessionmaker

from goblinshift.files.storage import FileStorage
from goblinshift.runs.lifecycle import CancelRequested, Refused, Status, decide_transition
from goblinshift.runs.store import (
    Applied,
    apply_transition,
    complete_after_commit,
    load_run,
    run_transaction,
)


class RunNotFoundError(LookupError):
    """Inexistente ou de outra conta: a mesma resposta nos dois casos (→ 404)."""

    def __init__(self) -> None:
        super().__init__("execução não encontrada")


@dataclass(frozen=True)
class CancelResult:
    run_id: uuid.UUID
    status: Status
    error_code: str | None

    @property
    def is_terminal(self) -> bool:
        """`False` enquanto o executor ainda está sendo parado (resposta 202)."""
        return self.status not in {Status.QUEUED, Status.RUNNING, Status.STOPPING}


def cancel_run(
    factory: sessionmaker[Session],
    storage: FileStorage,
    owner_id: uuid.UUID,
    run_id: uuid.UUID,
) -> CancelResult:
    def cancel(session: Session) -> tuple[CancelResult, Applied | None]:
        loaded = load_run(session, run_id, owner_id=owner_id)
        if loaded is None:
            raise RunNotFoundError
        decision = decide_transition(loaded.state, CancelRequested())
        if isinstance(decision, Refused):
            # Terminal: o estado real vale, inclusive um sucesso confirmado antes.
            state = loaded.state
            return CancelResult(run_id, state.status, state.error_code), None
        applied = apply_transition(session, loaded, decision, attempt_error=None)
        state = applied.state
        return CancelResult(run_id, state.status, state.error_code), applied

    result, applied = run_transaction(factory, cancel)
    if applied is not None:
        complete_after_commit(factory, storage, applied)
    return result
