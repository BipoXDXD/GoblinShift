"""`load_run` depois de esperar a trava da Run, em READ COMMITTED (3.4 e 3.5 do plano).

Quem espera a trava precisa enxergar a Attempt que a transação anterior encerrou. Se a Run voltar
nova com a Attempt velha, o cancelamento concorrente dá erro interno e o fencing aprova uma
tentativa já encerrada.
"""

import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.files.storage import FileStorage
from goblinshift.runs.cancellation import cancel_run
from goblinshift.runs.lifecycle import (
    MIN_RETRY_JITTER,
    Event,
    LeaseExpired,
    Phase,
    Published,
    PublishOutcome,
    Refused,
    Status,
    decide_transition,
)
from goblinshift.runs.store import (
    ACTIVE_STATUSES,
    Fence,
    LoadedRun,
    LostFencing,
    apply_transition,
    load_run,
    lock_fenced,
)
from tests.runs.worker_support import claim, coordinator, create_user, local_runner, queue_run

pytestmark = pytest.mark.integration

# Abaixo do `lock_timeout` de 2 s do `make_engine`: a segunda sessão ainda está esperando.
LOCK_WAIT_DEADLINE_S = 1.5
RESULT_WAIT_S = 10.0


def wait_until_someone_waits_for_a_lock(factory: sessionmaker[Session]) -> None:
    deadline = time.monotonic() + LOCK_WAIT_DEADLINE_S
    with factory() as session:
        while time.monotonic() < deadline:
            waiting = session.execute(
                text(
                    "SELECT count(*) FROM pg_stat_activity"
                    " WHERE datname = current_database() AND wait_event_type = 'Lock'"
                )
            ).scalar_one()
            session.rollback()
            if waiting:
                return
            threading.Event().wait(0.01)
    raise AssertionError("a segunda transação não chegou a esperar a trava da Run")


def apply_decision(
    session: Session,
    loaded: LoadedRun,
    event: Event,
    publish: Callable[[Session], None] | None = None,
) -> None:
    decision = decide_transition(loaded.state, event)
    assert not isinstance(decision, Refused)
    apply_transition(session, loaded, decision, attempt_error=None, publish=publish)


def test_cancelar_execucao_que_publicou_durante_a_espera_devolve_o_estado_real(
    factory: sessionmaker[Session], storage: FileStorage, work_root: Path
) -> None:
    owner_id = create_user(factory)
    run_id = queue_run(factory, storage, owner_id)
    worker = coordinator(factory, storage, local_runner(work_root))
    claimed = claim(factory, storage)
    worker.record_start(claimed)
    worker.reach_phase(claimed, Phase.PACKAGING)
    worker.reach_phase(claimed, Phase.FINALIZING)
    fence = Fence(run_id, claimed.generation, claimed.attempt_id)

    with ThreadPoolExecutor(1) as pool:
        with factory() as session, session.begin():
            loaded = lock_fenced(session, fence, ACTIVE_STATUSES)
            apply_decision(
                session,
                loaded,
                Published(claimed.generation, PublishOutcome.SUCCEEDED, loaded.now),
                publish=lambda _session: None,
            )
            canceled = pool.submit(cancel_run, factory, storage, owner_id, run_id)
            wait_until_someone_waits_for_a_lock(factory)
        result = canceled.result(timeout=RESULT_WAIT_S)

    assert (result.status, result.error_code) == (Status.SUCCEEDED, None)


def test_fencing_recusa_tentativa_encerrada_enquanto_esperava_a_trava(
    factory: sessionmaker[Session], storage: FileStorage, work_root: Path
) -> None:
    run_id = queue_run(factory, storage, create_user(factory))
    worker = coordinator(factory, storage, local_runner(work_root))
    claimed = claim(factory, storage)

    with ThreadPoolExecutor(1) as pool:
        with factory() as session, session.begin():
            loaded = load_run(session, run_id)
            assert loaded is not None
            apply_decision(
                session,
                loaded,
                LeaseExpired(claimed.generation, loaded.now, False, MIN_RETRY_JITTER),
            )
            may_start = pool.submit(worker.may_start, claimed)
            wait_until_someone_waits_for_a_lock(factory)
        with pytest.raises(LostFencing):
            may_start.result(timeout=RESULT_WAIT_S)


def test_skip_locked_pula_a_run_travada_sem_esperar(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    run_id = queue_run(factory, storage, create_user(factory))

    with factory() as holder, holder.begin():
        assert load_run(holder, run_id) is not None
        with factory() as other, other.begin():
            assert load_run(other, run_id, skip_locked=True) is None
