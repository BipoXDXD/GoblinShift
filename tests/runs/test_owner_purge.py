"""Fim da conta em `runs`: cancelar a execução ativa e purgar o histórico (1.8; Frente C)."""

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import Table, func, select, update
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.files.artifacts import create_candidate_object
from goblinshift.files.storage import FileStorage
from goblinshift.files.tables import object_use, stored_object
from goblinshift.platform.db import unit_of_work
from goblinshift.runs.lifecycle import Status
from goblinshift.runs.purge import cancel_active_run, purge_owner_batch
from goblinshift.runs.tables import attempt, idempotency_record, run, run_input, run_output
from tests.runs.support import insert_finished_run
from tests.runs.worker_support import claim, coordinator, create_user, local_runner, queue_run

pytestmark = pytest.mark.integration

LIMIT = 100
CREATED = datetime(2026, 10, 14, 9, 0, tzinfo=UTC)


def purge_batch(factory: sessionmaker[Session], owner_id: uuid.UUID, limit: int = LIMIT) -> bool:
    with unit_of_work(factory) as session:
        return purge_owner_batch(session, owner_id, limit)


def count(factory: sessionmaker[Session], table: Table, owner_id: uuid.UUID) -> int:
    with factory() as session:
        value: int = session.execute(
            select(func.count()).select_from(table).where(table.c.owner_id == owner_id)
        ).scalar_one()
    return value


def succeeded_run(
    factory: sessionmaker[Session], storage: FileStorage, work_root: Path, owner_id: uuid.UUID
) -> uuid.UUID:
    """Execução real até o fim: entradas, tentativa, saídas publicadas e usos liberados."""
    run_id = queue_run(factory, storage, owner_id)
    coordinator(factory, storage, local_runner(work_root)).run(claim(factory, storage))
    with factory() as session:
        status = session.execute(select(run.c.status).where(run.c.id == run_id)).scalar_one()
    assert status == Status.SUCCEEDED
    return run_id


def test_conta_sem_execucoes_termina_na_hora(factory: sessionmaker[Session]) -> None:
    assert purge_batch(factory, create_user(factory)) is True


def test_apaga_execucao_terminal_com_entradas_saidas_tentativas_e_idempotencia(
    factory: sessionmaker[Session], storage: FileStorage, work_root: Path
) -> None:
    owner = create_user(factory)
    run_id = succeeded_run(factory, storage, work_root, owner)
    with unit_of_work(factory) as session:
        session.execute(
            idempotency_record.insert().values(
                owner_id=owner,
                operation="create_run",
                idem_key="k" * 16,
                command_hash=b"\x00" * 32,
                run_id=run_id,
                expires_at=func.now() + timedelta(hours=24),
            )
        )

    assert purge_batch(factory, owner) is True

    for table in (run, run_input, run_output, idempotency_record):
        assert count(factory, table, owner) == 0
    with factory() as session:
        attempts = session.execute(
            select(func.count()).select_from(attempt).where(attempt.c.run_id == run_id)
        ).scalar_one()
    assert attempts == 0


def test_libera_usos_que_ficaram_abertos_na_execucao_terminal(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    # A transação que libera os usos depois do término não chegou: o objeto ficaria preso.
    owner = create_user(factory)
    run_id = queue_run(factory, storage, owner)
    with unit_of_work(factory) as session:
        session.execute(
            update(run)
            .where(run.c.id == run_id)
            .values(
                status=Status.CANCELED,
                stop_reason="CANCELED",
                error_code="CANCELED",
                finished_at=func.now(),
            )
        )

    assert purge_batch(factory, owner) is True

    with factory() as session:
        open_uses = session.execute(
            select(func.count())
            .select_from(object_use)
            .where(object_use.c.holder_id == run_id, object_use.c.ended_at.is_(None))
        ).scalar_one()
    assert open_uses == 0


def test_execucao_nao_terminal_nao_e_apagada(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = create_user(factory)
    insert_finished_run(factory, owner, CREATED)
    active = queue_run(factory, storage, owner)

    assert purge_batch(factory, owner) is False

    with factory() as session:
        remaining = list(session.execute(select(run.c.id).where(run.c.owner_id == owner)).scalars())
    assert remaining == [active]


def test_tentativa_com_saida_pendente_espera_o_descarte(
    factory: sessionmaker[Session], storage: FileStorage, work_root: Path
) -> None:
    # Apagar a tentativa antes do descarte deixaria o arquivo pendente sem quem o remova.
    owner = create_user(factory)
    run_id = succeeded_run(factory, storage, work_root, owner)
    with factory() as session:
        attempt_id = session.execute(
            select(attempt.c.id).where(attempt.c.run_id == run_id)
        ).scalar_one()
    with unit_of_work(factory) as session:
        pending = create_candidate_object(session, owner, run_id, attempt_id, 9)

    assert purge_batch(factory, owner) is False
    assert count(factory, run, owner) == 1

    with unit_of_work(factory) as session:
        session.execute(stored_object.delete().where(stored_object.c.id == pending.object_id))
    assert purge_batch(factory, owner) is True


def test_lote_pequeno_avanca_ate_terminar(factory: sessionmaker[Session]) -> None:
    owner = create_user(factory)
    for minutes in range(3):
        insert_finished_run(factory, owner, CREATED + timedelta(minutes=minutes))

    assert purge_batch(factory, owner, limit=1) is False
    assert count(factory, run, owner) == 2
    assert purge_batch(factory, owner, limit=1) is False
    assert purge_batch(factory, owner, limit=1) is True
    assert count(factory, run, owner) == 0


def test_outra_conta_fica_intacta(factory: sessionmaker[Session]) -> None:
    owner = create_user(factory, "ana")
    other = create_user(factory, "bia")
    insert_finished_run(factory, owner, CREATED)
    kept = insert_finished_run(factory, other, CREATED)

    purge_batch(factory, owner)

    with factory() as session:
        remaining = list(session.execute(select(run.c.id).where(run.c.owner_id == other)).scalars())
    assert remaining == [kept]


# --- Cancelamento da execução ativa -----------------------------------------------------------


def test_cancela_a_execucao_na_fila(factory: sessionmaker[Session], storage: FileStorage) -> None:
    owner = create_user(factory)
    run_id = queue_run(factory, storage, owner)

    result = cancel_active_run(factory, storage, owner)

    assert result is not None
    assert result.run_id == run_id
    assert result.status == Status.CANCELED


def test_sem_execucao_ativa_nao_cancela_nada(factory: sessionmaker[Session]) -> None:
    owner = create_user(factory)
    finished = insert_finished_run(factory, owner, CREATED)

    assert cancel_active_run(factory, FileStorage(Path("/nao-usado")), owner) is None

    with factory() as session:
        status = session.execute(select(run.c.status).where(run.c.id == finished)).scalar_one()
    assert status == Status.SUCCEEDED


def test_cancelamento_nao_toca_execucao_de_outra_conta(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = create_user(factory, "ana")
    other_run = queue_run(factory, storage, create_user(factory, "bia"))

    assert cancel_active_run(factory, storage, owner) is None

    with factory() as session:
        status = session.execute(select(run.c.status).where(run.c.id == other_run)).scalar_one()
    assert status == Status.QUEUED
