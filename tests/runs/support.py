"""Construtores de dados dos testes de execuções."""

import uuid
from collections.abc import Sequence
from datetime import datetime, timedelta

from sqlalchemy import func, insert, select, update
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.files.storage import FileStorage
from goblinshift.files.tables import object_use
from goblinshift.platform.db import unit_of_work
from goblinshift.runs.acceptance import RunCommand
from goblinshift.runs.lifecycle import Status
from goblinshift.runs.tables import idempotency_record, run, run_input
from tests.files.support import create_user, ready_upload

RECIPE_ID = "csv-janitor"
RECIPE_VERSION = 1


def new_owner(factory: sessionmaker[Session]) -> uuid.UUID:
    return create_user(factory, f"conta-{uuid.uuid4()}")


def ready_inputs(
    factory: sessionmaker[Session], storage: FileStorage, owner_id: uuid.UUID, count: int = 1
) -> list[uuid.UUID]:
    return [ready_upload(factory, storage, owner_id, b"a,b\n1,2\n").id for _ in range(count)]


def run_command(upload_ids: Sequence[uuid.UUID], parameters: object | None = None) -> RunCommand:
    return RunCommand(
        recipe_id=RECIPE_ID,
        recipe_version=RECIPE_VERSION,
        input_upload_ids=tuple(upload_ids),
        parameters={} if parameters is None else parameters,
    )


def new_key() -> str:
    return str(uuid.uuid4())


def count_rows(factory: sessionmaker[Session]) -> dict[str, int]:
    """Linhas que uma aceitação cria; um replay ou uma recusa não muda nenhuma."""
    tables = {
        "run": run,
        "run_input": run_input,
        "object_use": object_use,
        "idempotency_record": idempotency_record,
    }
    with factory() as session:
        return {
            name: session.execute(select(func.count()).select_from(table)).scalar_one()
            for name, table in tables.items()
        }


def finish_run(factory: sessionmaker[Session], run_id: uuid.UUID) -> None:
    """Leva a execução a um estado terminal, liberando a vaga da conta."""
    with unit_of_work(factory) as session:
        session.execute(
            update(run)
            .where(run.c.id == run_id)
            .values(
                status=Status.CANCELED,
                stop_reason="CANCELED",
                error_code="CANCELED",
                cancel_requested_at=func.now(),
                finished_at=func.now(),
            )
        )


def insert_finished_run(
    factory: sessionmaker[Session],
    owner_id: uuid.UUID,
    created_at: datetime,
    *,
    recipe_id: str = RECIPE_ID,
    status: Status = Status.SUCCEEDED,
    error_code: str | None = None,
) -> uuid.UUID:
    """Execução já terminada, com `created_at` escolhido: o histórico sem passar pela fila."""
    with unit_of_work(factory) as session:
        run_id: uuid.UUID = session.execute(
            insert(run)
            .values(
                owner_id=owner_id,
                recipe_id=recipe_id,
                recipe_version=RECIPE_VERSION,
                executor_digest="local-dev",
                parameters={},
                limits={},
                status=status,
                error_code=error_code,
                stop_reason=error_code if status is Status.CANCELED else None,
                queue_expires_at=created_at + timedelta(hours=2),
                eligible_at=created_at,
                created_at=created_at,
                finished_at=created_at + timedelta(minutes=1),
            )
            .returning(run.c.id)
        ).scalar_one()
    return run_id
