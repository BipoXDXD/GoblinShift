"""Lotes de manutenção escolhem as chaves numa CTE materializada (sem banco: só o SQL compilado).

`DELETE`/`UPDATE ... WHERE id IN (SELECT ... LIMIT n FOR UPDATE SKIP LOCKED)` depende do plano:
reexecutado, o subplano trava outras linhas e o lote passa do teto. A reexecução não se reproduz
de forma determinística, então a guarda é estrutural; o comportamento fica nos testes com PG.
"""

import uuid
from collections.abc import Callable
from datetime import UTC, datetime

import pytest
from sqlalchemy import ColumnElement, false, select
from sqlalchemy.orm import Session

from goblinshift.capacity import delete_released_reservations
from goblinshift.files.purge import purge_owner_batch
from goblinshift.files.retention import HistoryReferences, delete_old_file_rows
from goblinshift.identity.login_state import purge_expired_states
from goblinshift.identity.purge import (
    delete_ended_web_sessions,
    delete_expired_login_states,
    delete_old_audit_events,
)
from goblinshift.identity.rate_limit import delete_expired_windows
from goblinshift.identity.tables import login_state
from goblinshift.platform.sql import skip_locked_batch
from goblinshift.runs.retention import delete_expired_idempotency_records
from tests.sql_capture import POSTGRESQL, RecordingSession

NOW = datetime(2026, 11, 10, tzinfo=UTC)
LIMIT = 7


def _not_in_history(_id: ColumnElement[uuid.UUID]) -> ColumnElement[bool]:
    return false()


def _upload_not_in_history(
    _owner_id: ColumnElement[uuid.UUID], _id: ColumnElement[uuid.UUID]
) -> ColumnElement[bool]:
    return false()


BATCHES: dict[str, tuple[Callable[[Session], object], str]] = {
    "capacity.delete_released_reservations": (
        lambda s: delete_released_reservations(s, NOW, LIMIT),
        "DELETE FROM resource_reservation",
    ),
    "files.purge (upload)": (
        lambda s: purge_owner_batch(s, uuid.uuid4(), LIMIT),
        "UPDATE upload SET deleted_at",
    ),
    "files.purge (artifact)": (
        lambda s: purge_owner_batch(s, uuid.uuid4(), LIMIT),
        "UPDATE artifact SET deleted_at",
    ),
    "files.retention (object_use)": (
        lambda s: delete_old_file_rows(
            s,
            NOW,
            LIMIT,
            HistoryReferences(upload=_upload_not_in_history, artifact=_not_in_history),
        ),
        "DELETE FROM object_use",
    ),
    "identity.login_state": (
        lambda s: delete_expired_login_states(s, NOW, LIMIT),
        "DELETE FROM login_state",
    ),
    "identity.web_session": (
        lambda s: delete_ended_web_sessions(s, NOW, LIMIT),
        "DELETE FROM web_session",
    ),
    "identity.login_state.purge_expired_states": (
        lambda s: purge_expired_states(s, NOW, limit=LIMIT),
        "DELETE FROM login_state",
    ),
    "identity.request_window": (
        lambda s: delete_expired_windows(s, NOW, LIMIT),
        "DELETE FROM request_window",
    ),
    "runs.idempotency_record": (
        lambda s: delete_expired_idempotency_records(s, NOW, LIMIT),
        "DELETE FROM idempotency_record",
    ),
}


@pytest.mark.parametrize("name", BATCHES)
def test_lote_escolhe_as_chaves_numa_cte_materializada(name: str) -> None:
    run_batch, statement_start = BATCHES[name]
    recorder = RecordingSession()

    run_batch(recorder.as_session())

    matching = [sql for sql in recorder.postgresql_sql() if statement_start in sql]
    assert len(matching) == 1, recorder.postgresql_sql()
    [sql] = matching
    assert sql.startswith("WITH chosen AS MATERIALIZED")
    cte, _, outer = sql.partition(statement_start)
    assert "LIMIT" in cte
    assert "FOR UPDATE SKIP LOCKED" in cte
    assert "SKIP LOCKED" not in outer


def test_skip_locked_batch_aplica_teto_trava_e_materializa() -> None:
    chosen = skip_locked_batch(select(login_state.c.key).order_by(login_state.c.key), 3)

    sql = str(
        select(chosen.c.key).compile(dialect=POSTGRESQL, compile_kwargs={"literal_binds": True})
    )

    assert sql.startswith("WITH chosen AS MATERIALIZED")
    assert "ORDER BY login_state.key \n LIMIT 3 FOR UPDATE SKIP LOCKED" in sql


def test_retencao_do_audit_event_escolhe_o_lote_sem_travar_linhas() -> None:
    # O papel da manutenção não tem UPDATE no audit_event (ADR 055), e `FOR UPDATE` exige UPDATE.
    # O lote continua numa CTE materializada, para o teto valer.
    recorder = RecordingSession()

    delete_old_audit_events(recorder.as_session(), NOW, LIMIT)

    [sql] = [sql for sql in recorder.postgresql_sql() if "DELETE FROM audit_event" in sql]
    cte, _, _ = sql.partition("DELETE FROM audit_event")
    assert cte.startswith("WITH chosen AS MATERIALIZED")
    assert "LIMIT" in cte
    assert "FOR UPDATE" not in sql
