"""O ciclo da manutenção aplica a retenção longa (1.7) e mede o que os alertas de causa leem (3.7).

Ponta a ponta com PostgreSQL real: uma Run de 100 dias com entrada usada sai inteira em um ciclo
(Run, tentativa, entrada, upload e objeto), na ordem segura com o disco, e o ciclo devolve o
tamanho do que ainda falta. Também os medidores dos alertas: Run presa em `stopping`, limitador
de exclusões e capacidade global.
"""

import uuid
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.files.objects import release
from goblinshift.files.states import HolderKind, ObjectState
from goblinshift.files.storage import FileStorage
from goblinshift.files.tables import object_use, stored_object, upload
from goblinshift.files.uploads import request_deletion
from goblinshift.platform.db import unit_of_work
from goblinshift.runs.lifecycle import MIN_RETRY_JITTER
from goblinshift.runs.maintenance import Maintenance, MaintenanceConfig
from goblinshift.runs.tables import attempt, run, run_input
from tests.runs.worker_support import claim, create_user, local_runner, queue_run

pytestmark = pytest.mark.integration


def maintenance(
    factory: sessionmaker[Session], storage: FileStorage, work_root: Path
) -> Maintenance:
    config = MaintenanceConfig(stop_grace=timedelta(seconds=2), jitter=lambda: MIN_RETRY_JITTER)
    return Maintenance(factory, storage, local_runner(work_root), config)


def finished_long_ago(
    factory: sessionmaker[Session], storage: FileStorage, days: int
) -> tuple[uuid.UUID, uuid.UUID]:
    """Run terminada há `days` dias, com a entrada já excluída e fora do disco."""
    owner = create_user(factory, f"conta-{uuid.uuid4()}")
    run_id = queue_run(factory, storage, owner)
    ago = func.now() - timedelta(days=days)
    with unit_of_work(factory) as session:
        release(session, HolderKind.RUN, run_id)
        session.execute(
            update(object_use).where(object_use.c.holder_id == run_id).values(ended_at=ago)
        )
        session.execute(
            update(run)
            .where(run.c.id == run_id)
            .values(status="succeeded", created_at=ago, finished_at=ago)
        )
        upload_id = session.execute(
            select(run_input.c.upload_id).where(run_input.c.run_id == run_id)
        ).scalar_one()
        request_deletion(session, owner, upload_id)
        session.execute(update(upload).where(upload.c.id == upload_id).values(created_at=ago))
        session.execute(
            update(stored_object)
            .where(
                stored_object.c.id
                == select(upload.c.object_id).where(upload.c.id == upload_id).scalar_subquery()
            )
            .values(state=ObjectState.DELETED)
        )
    return run_id, upload_id


def count(factory: sessionmaker[Session], table: object, where: object) -> int:
    with factory() as session:
        return session.execute(
            select(func.count()).select_from(table).where(where)  # type: ignore[arg-type]
        ).scalar_one()


def test_ciclo_remove_a_run_de_90_dias_e_depois_as_linhas_dos_arquivos(
    factory: sessionmaker[Session], storage: FileStorage, work_root: Path
) -> None:
    run_id, upload_id = finished_long_ago(factory, storage, days=100)

    counts = maintenance(factory, storage, work_root).run_cycle()

    assert counts["runs_deleted"] == 1
    assert counts["file_rows_deleted"] == 1
    assert count(factory, run, run.c.id == run_id) == 0
    assert count(factory, upload, upload.c.id == upload_id) == 0
    assert counts["runs_to_delete"] == 0


def test_ciclo_anula_a_run_de_40_dias_e_mantem_a_linha_do_upload(
    factory: sessionmaker[Session], storage: FileStorage, work_root: Path
) -> None:
    """O histórico ainda aponta para o upload: o nome sai, a linha fica até os 90 dias."""
    run_id, upload_id = finished_long_ago(factory, storage, days=40)

    counts = maintenance(factory, storage, work_root).run_cycle()

    assert (counts["runs_anonymized"], counts["uploads_anonymized"]) == (1, 1)
    assert counts["file_rows_deleted"] == 0
    with factory() as session:
        name = session.execute(
            select(upload.c.original_name).where(upload.c.id == upload_id)
        ).scalar_one()
    assert name is None
    assert count(factory, run, run.c.id == run_id) == 1
    assert (counts["runs_to_anonymize"], counts["uploads_to_anonymize"]) == (0, 0)


@pytest.mark.parametrize(("minutes_ago", "stuck"), [(11, 1), (9, 0)])
def test_ciclo_conta_run_presa_em_stopping_ha_mais_de_10_min(
    factory: sessionmaker[Session],
    storage: FileStorage,
    work_root: Path,
    minutes_ago: int,
    stuck: int,
) -> None:
    run_id = queue_run(factory, storage, create_user(factory))
    claimed = claim(factory, storage)
    with unit_of_work(factory) as session:
        session.execute(
            update(run)
            .where(run.c.id == run_id)
            .values(
                status="stopping",
                phase=None,
                stop_reason="CANCELED",
                cancel_requested_at=func.now() - timedelta(minutes=minutes_ago),
            )
        )
        # Lease vivo: a reconciliação não mexe, como num executor que não confirma a parada.
        session.execute(
            update(attempt)
            .where(attempt.c.id == claimed.attempt_id)
            .values(lease_expires_at=func.now() + timedelta(hours=1))
        )

    counts = maintenance(factory, storage, work_root).run_cycle()

    assert counts["runs_stuck_stopping"] == stuck


def test_ciclo_mede_a_capacidade_global_e_o_limitador(
    factory: sessionmaker[Session], storage: FileStorage, work_root: Path
) -> None:
    queue_run(factory, storage, create_user(factory))

    counts = maintenance(factory, storage, work_root).run_cycle()

    assert counts["storage_global_max_bytes"] == 20 * 1024**3
    assert counts["storage_global_reserved_bytes"] > 0
    assert counts["purge_limit_reached"] == 0
