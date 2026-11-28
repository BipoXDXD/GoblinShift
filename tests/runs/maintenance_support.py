"""Apoio dos testes da purga de contas: o que a conta ainda tem no banco, quantos ciclos a
manutenção leva para purgá-la e os canceladores que a purga recebe."""

import uuid
from collections.abc import Callable
from functools import partial

from sqlalchemy import Table, func, select
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.entrypoints.maintenance import AccountJobs
from goblinshift.files.storage import FileStorage
from goblinshift.files.tables import artifact, stored_object, transfer_slot, upload
from goblinshift.identity.tables import api_key, app_user, request_window, web_session
from goblinshift.runs.maintenance import Maintenance
from goblinshift.runs.purge import cancel_active_run
from goblinshift.runs.tables import idempotency_record, run, run_input, run_output

MAX_CYCLES = 10

OWNED_TABLES: dict[str, tuple[Table, str]] = {
    "app_user": (app_user, "id"),
    "api_key": (api_key, "owner_id"),
    "web_session": (web_session, "user_id"),
    "run": (run, "owner_id"),
    "run_input": (run_input, "owner_id"),
    "run_output": (run_output, "owner_id"),
    "idempotency_record": (idempotency_record, "owner_id"),
    "upload": (upload, "owner_id"),
    "artifact": (artifact, "owner_id"),
    "stored_object": (stored_object, "owner_id"),
    "transfer_slot": (transfer_slot, "owner_id"),
    "request_window": (request_window, "owner_id"),
}


def count_owned(factory: sessionmaker[Session], owner_id: uuid.UUID) -> dict[str, int]:
    with factory() as session:
        return {
            name: session.execute(
                select(func.count()).select_from(table).where(table.c[column] == owner_id)
            ).scalar_one()
            for name, (table, column) in OWNED_TABLES.items()
        }


def cycles_until_purged(
    factory: sessionmaker[Session],
    maintenance: Maintenance,
    jobs: AccountJobs,
    owner_id: uuid.UUID,
) -> int:
    for cycle in range(1, MAX_CYCLES + 1):
        maintenance.run_cycle()
        jobs.run_cycle()
        if count_owned(factory, owner_id)["app_user"] == 0:
            return cycle
    raise AssertionError("a conta não foi purgada")


def real_canceller(
    factory: sessionmaker[Session], storage: FileStorage
) -> Callable[[uuid.UUID], object]:
    """O cancelador que `serve` passa à purga, sem o `run.finished`."""
    return partial(cancel_active_run, factory, storage, on_finished=None)


def keep_run(_owner_id: uuid.UUID) -> None:
    """Cancelador que não cancela: para os testes em que a Run da conta não importa ou precisa
    seguir ativa."""
