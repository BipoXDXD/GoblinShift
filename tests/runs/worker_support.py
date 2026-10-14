"""Construtores e leituras dos testes do worker e da manutenção (fluxo depois da aceitação).

A aceitação (`POST /runs`) é de outro módulo; aqui a Run entra na fila como a aceitação a
deixaria: `queued`, entradas vinculadas e usos registrados com `hold`.
"""

import sys
import uuid
from collections.abc import Sequence
from datetime import timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import func, insert, select, text, update
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.catalog import CATALOG
from goblinshift.files.objects import hold
from goblinshift.files.states import HolderKind
from goblinshift.files.storage import FileStorage
from goblinshift.files.tables import object_use, upload
from goblinshift.files.uploads import create_intents, receive_content
from goblinshift.identity.tables import app_user
from goblinshift.platform.db import unit_of_work
from goblinshift.platform.settings import LOCAL_EXECUTOR_IMAGE
from goblinshift.runs.lifecycle import MIN_RETRY_JITTER, QUEUE_WINDOW
from goblinshift.runs.tables import attempt, run, run_input
from goblinshift.runs.worker.claim import ClaimedAttempt, claim_next
from goblinshift.runs.worker.coordinator import Coordinator, CoordinatorConfig
from goblinshift.runs.worker.runner import LocalRunner, Runner

VALID = b"nome,cidade\r\nAna,Recife\r\n"
INVALID = b'nome,cidade\r\n"Ana,Recife\r\n'
LEASE = timedelta(seconds=2)
FAKE_EXECUTOR = str(Path(__file__).with_name("fake_executor.py"))


def create_user(factory: sessionmaker[Session], subject: str = "ana") -> uuid.UUID:
    with unit_of_work(factory) as session:
        user_id: uuid.UUID = session.execute(
            insert(app_user)
            .values(issuer="test", subject=subject, display_name=subject)
            .returning(app_user.c.id)
        ).scalar_one()
    return user_id


def queue_run(
    factory: sessionmaker[Session],
    storage: FileStorage,
    owner_id: uuid.UUID,
    tables: Sequence[tuple[str, bytes]] = (("clientes.csv", VALID),),
    executor_image: str = LOCAL_EXECUTOR_IMAGE,
) -> uuid.UUID:
    upload_ids = []
    for name, data in tables:
        with unit_of_work(factory) as session:
            [intent] = create_intents(session, owner_id, [(name, len(data))])
        with factory() as session:
            receive_content(session, storage, owner_id, intent.id, [data])
        upload_ids.append(intent.id)
    with unit_of_work(factory) as session:
        run_id: uuid.UUID = session.execute(
            insert(run)
            .values(
                owner_id=owner_id,
                recipe_id="csv-janitor",
                recipe_version=1,
                executor_digest=executor_image,
                parameters=CATALOG.resolve("csv-janitor", 1, {}),
                limits=dict(CATALOG.get_version("csv-janitor", 1).limits),
                queue_expires_at=func.now() + QUEUE_WINDOW,
            )
            .returning(run.c.id)
        ).scalar_one()
        for position, upload_id in enumerate(upload_ids):
            session.execute(
                insert(run_input).values(
                    run_id=run_id, position=position, owner_id=owner_id, upload_id=upload_id
                )
            )
        object_ids = session.execute(
            select(upload.c.object_id).where(upload.c.id.in_(upload_ids))
        ).scalars()
        hold(session, list(object_ids), HolderKind.RUN, run_id)
    return run_id


def local_runner(work_root: Path, *mode: str) -> LocalRunner:
    """Executor real por padrão; com `mode`, o executor de teste de `fake_executor.py`."""
    if not mode:
        return LocalRunner(work_root, attempt_timeout=timedelta(seconds=30))
    return LocalRunner(
        work_root,
        attempt_timeout=timedelta(seconds=30),
        command=(sys.executable, FAKE_EXECUTOR, *mode),
    )


def coordinator(
    factory: sessionmaker[Session],
    storage: FileStorage,
    runner: Runner,
    attempt_timeout: timedelta = timedelta(minutes=10),
) -> Coordinator:
    config = CoordinatorConfig(
        lease=LEASE,
        status_poll=timedelta(seconds=0.05),
        stop_grace=timedelta(seconds=2),
        attempt_timeout=attempt_timeout,
        jitter=lambda: MIN_RETRY_JITTER,
    )
    return Coordinator(factory, storage, runner, config)


def claim(factory: sessionmaker[Session], storage: FileStorage) -> ClaimedAttempt:
    claimed = claim_next(factory, storage, LEASE)
    assert claimed is not None
    return claimed


def run_row(factory: sessionmaker[Session], run_id: uuid.UUID) -> dict[str, Any]:
    with factory() as session:
        return dict(session.execute(select(run).where(run.c.id == run_id)).one()._mapping)


def attempts(factory: sessionmaker[Session], run_id: uuid.UUID) -> list[dict[str, Any]]:
    with factory() as session:
        rows = session.execute(
            select(attempt).where(attempt.c.run_id == run_id).order_by(attempt.c.number)
        ).all()
    return [dict(row._mapping) for row in rows]


def open_run_uses(factory: sessionmaker[Session], run_id: uuid.UUID) -> int:
    with factory() as session:
        count: int = session.execute(
            select(func.count())
            .select_from(object_use)
            .where(object_use.c.holder_id == run_id, object_use.c.ended_at.is_(None))
        ).scalar_one()
    return count


def make_eligible_now(factory: sessionmaker[Session], run_id: uuid.UUID) -> None:
    """Pula a espera de retentativa (5 a 30 s) sem mexer em mais nada."""
    with unit_of_work(factory) as session:
        session.execute(update(run).where(run.c.id == run_id).values(eligible_at=func.now()))


def expire_lease(factory: sessionmaker[Session], run_id: uuid.UUID) -> None:
    with unit_of_work(factory) as session:
        session.execute(
            update(attempt)
            .where(attempt.c.run_id == run_id, attempt.c.ended_at.is_(None))
            .values(lease_expires_at=func.now() - timedelta(seconds=1))
        )


def snapshot(factory: sessionmaker[Session]) -> list[Any]:
    """Todas as linhas das tabelas que o coordenador escreve, para comparar antes e depois."""
    tables = [
        "run",
        "attempt",
        "run_input",
        "run_output",
        "artifact",
        "stored_object",
        "object_use",
    ]
    with factory() as session:
        return [
            sorted(map(tuple, session.execute(text(f"SELECT * FROM {name}")).all()), key=str)  # noqa: S608
            for name in tables
        ]
