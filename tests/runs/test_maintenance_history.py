"""O ciclo da manutenção aplica a retenção longa (1.7) e mede o que os alertas de causa leem (3.7).

Ponta a ponta com PostgreSQL real: uma Run de 100 dias com entrada usada sai inteira em um ciclo
(Run, tentativa, entrada, upload e objeto), na ordem segura com o disco, e o ciclo devolve o
tamanho do que ainda falta. Também os medidores dos alertas: Run presa em `stopping`, limitador
de exclusões e capacidade global.
"""

import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg
import pytest
from psycopg import errors as psycopg_errors
from sqlalchemy import func, insert, select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from goblinshift import capacity
from goblinshift.capacity.tables import resource_reservation as capacity_reservation
from goblinshift.files.limits import EXPIRY_BATCH_SIZE
from goblinshift.files.objects import release
from goblinshift.files.states import ArtifactRole, HolderKind, ObjectState
from goblinshift.files.storage import FileStorage
from goblinshift.files.tables import artifact, object_use, stored_object, upload
from goblinshift.files.uploads import request_deletion
from goblinshift.platform.db import unit_of_work
from goblinshift.runs import retention as run_retention
from goblinshift.runs.lifecycle import MIN_RETRY_JITTER
from goblinshift.runs.maintenance import STEPS_FAILED, Maintenance, MaintenanceConfig
from goblinshift.runs.tables import attempt, run, run_input, run_output
from tests.capacity.support import reserve_now
from tests.runs.support import insert_finished_run
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
            .values(state=ObjectState.DELETED, created_at=ago)
        )
    return run_id, upload_id


def old_finished_run(factory: sessionmaker[Session], days: int) -> uuid.UUID:
    """Run terminada há `days` dias, sem entradas: só a linha da Run no histórico."""
    owner = create_user(factory, f"conta-{uuid.uuid4()}")
    return insert_finished_run(factory, owner, datetime.now(UTC) - timedelta(days=days))


def query_canceled() -> Exception:
    """Como o SQLAlchemy entrega o `statement_timeout` (57014): o banco respondeu."""
    return DBAPIError.instance("SELECT 1", {}, psycopg_errors.QueryCanceled("x"), psycopg.Error)


def raising(error: Exception) -> Callable[..., int]:
    def fail(*_: object) -> int:
        raise error

    return fail


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


def test_ciclo_anula_os_nomes_das_saidas_da_run_de_40_dias(
    factory: sessionmaker[Session], storage: FileStorage, work_root: Path
) -> None:
    run_id, upload_id = finished_long_ago(factory, storage, days=40)
    with unit_of_work(factory) as session:
        owner, object_id = session.execute(
            select(upload.c.owner_id, upload.c.object_id).where(upload.c.id == upload_id)
        ).one()
        # Saída que reaproveita o objeto da entrada (Detox), já vencida e excluída.
        artifact_id = session.execute(
            insert(artifact)
            .values(
                owner_id=owner,
                object_id=object_id,
                role="output",
                file_name="cliente.csv",
                expires_at=func.now(),
                deleted_at=func.now(),
            )
            .returning(artifact.c.id)
        ).scalar_one()
        session.execute(
            insert(run_output).values(
                run_id=run_id,
                position=0,
                owner_id=owner,
                artifact_id=artifact_id,
                logical_name="cliente.csv",
            )
        )

    counts = maintenance(factory, storage, work_root).run_cycle()

    assert counts["outputs_anonymized"] == 1
    assert counts["outputs_to_anonymize"] == 0
    with factory() as session:
        names = session.execute(
            select(run_output.c.logical_name, artifact.c.file_name).join(
                artifact, artifact.c.id == run_output.c.artifact_id
            )
        ).one()
    assert tuple(names) == (None, None)


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


@pytest.mark.parametrize(("minutes_ago", "stuck"), [(11, 1), (9, 0)])
def test_run_parada_por_executor_indisponivel_conta_como_presa_pelo_pedido(
    factory: sessionmaker[Session],
    storage: FileStorage,
    work_root: Path,
    minutes_ago: int,
    stuck: int,
) -> None:
    # Sem cancelamento e com o prazo daqui a 25 min: só o instante do pedido diz a idade.
    run_id = queue_run(factory, storage, create_user(factory))
    claimed = claim(factory, storage)
    with unit_of_work(factory) as session:
        session.execute(
            update(run)
            .where(run.c.id == run_id)
            .values(
                status="stopping",
                phase=None,
                stop_reason="EXECUTOR_UNAVAILABLE",
                cancel_requested_at=None,
                deadline_at=func.now() + timedelta(minutes=25),
                stop_requested_at=func.now() - timedelta(minutes=minutes_ago),
            )
        )
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


def expired_artifacts(factory: sessionmaker[Session], how_many: int) -> None:
    """Artefatos vencidos há 1 hora, inseridos direto: o lote da expiração é o que se mede."""
    owner = create_user(factory, f"conta-{uuid.uuid4()}")
    with unit_of_work(factory) as session:
        object_ids = session.execute(
            insert(stored_object)
            .values(
                [
                    {
                        "owner_id": owner,
                        "storage_key": f"objects/{uuid.uuid4()}",
                        "state": ObjectState.COMMITTED,
                        "sha256": bytes(32),
                        "size_bytes": 1,
                    }
                    for _ in range(how_many)
                ]
            )
            .returning(stored_object.c.id)
        ).scalars()
        session.execute(
            insert(artifact).values(
                [
                    {
                        "owner_id": owner,
                        "object_id": object_id,
                        "role": ArtifactRole.OUTPUT,
                        "file_name": "saida.csv",
                        "expires_at": func.now() - timedelta(hours=1),
                    }
                    for object_id in object_ids
                ]
            )
        )


def test_ciclo_mede_501_artefatos_vencidos_alem_do_lote(
    factory: sessionmaker[Session], storage: FileStorage, work_root: Path
) -> None:
    expired_artifacts(factory, EXPIRY_BATCH_SIZE + 1)

    counts = maintenance(factory, storage, work_root).run_cycle()

    assert counts["artifacts_expired"] == 500
    assert counts["artifacts_to_expire"] == 1


def test_ciclo_mede_reservas_liberadas_alem_do_lote(
    factory: sessionmaker[Session], storage: FileStorage, work_root: Path
) -> None:
    owner = create_user(factory, f"conta-{uuid.uuid4()}")
    for _ in range(2):
        holder_id = reserve_now(factory, owner, 10)
        with unit_of_work(factory) as session:
            capacity.release(session, holder=capacity.HolderKind.UPLOAD, holder_id=holder_id)
            session.execute(
                update(capacity_reservation)
                .where(capacity_reservation.c.holder_id == holder_id)
                .values(released_at=func.now() - timedelta(days=91))
            )
    config = MaintenanceConfig(
        stop_grace=timedelta(seconds=2), jitter=lambda: MIN_RETRY_JITTER, retention_batch_size=1
    )

    counts = Maintenance(factory, storage, local_runner(work_root), config).run_cycle()

    assert counts["reservations_removed"] == 1
    assert counts["reservations_to_remove"] == 1


def test_timeout_na_anonimizacao_nao_impede_a_remocao_das_runs_antigas(
    factory: sessionmaker[Session],
    storage: FileStorage,
    work_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = old_finished_run(factory, days=91)
    monkeypatch.setattr(run_retention, "anonymize_finished_runs", raising(query_canceled()))

    counts = maintenance(factory, storage, work_root).run_cycle()

    assert counts[STEPS_FAILED] == 1
    assert "runs_anonymized" not in counts
    assert counts["runs_deleted"] == 1
    assert count(factory, run, run.c.id == run_id) == 0
