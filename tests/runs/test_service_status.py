"""Estado grosseiro do serviço (`/estado`, D9 do plano de paridade): só níveis, nunca contagens.

A classificação é função pura e roda sem banco; a leitura com o relógio do banco, as execuções
em fila e o lease vivo das tentativas é testada com PostgreSQL real (`integration`).
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, insert
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.platform.db import unit_of_work
from goblinshift.runs.acceptance import MAX_QUEUED_RUNS
from goblinshift.runs.lifecycle import Phase, Status
from goblinshift.runs.service_status import (
    SLOW_AFTER,
    STALLED_AFTER,
    ProcessingLevel,
    QueueLevel,
    QueueReading,
    ServiceStatus,
    classify,
    read_service_status,
)
from goblinshift.runs.tables import attempt, run
from tests.runs.support import new_owner

NOW = datetime(2026, 11, 28, 12, 0, tzinfo=UTC)


def reading(
    queued: int = 0, oldest_wait: timedelta | None = None, *, lease_alive: bool = False
) -> QueueReading:
    return QueueReading(queued=queued, oldest_wait=oldest_wait, lease_alive=lease_alive)


# --- Classificação (sem banco) ----------------------------------------------------------------


def test_fila_vazia_e_normal_e_livre() -> None:
    assert classify(reading(), NOW) == ServiceStatus(
        processing=ProcessingLevel.NORMAL, queue=QueueLevel.FREE, checked_at=NOW
    )


def test_fila_no_teto_da_aceitacao_e_cheia() -> None:
    status = classify(reading(MAX_QUEUED_RUNS, timedelta(seconds=5)), NOW)

    assert status.queue is QueueLevel.FULL


def test_fila_abaixo_do_teto_e_ocupada() -> None:
    status = classify(reading(MAX_QUEUED_RUNS - 1, timedelta(seconds=5)), NOW)

    assert status.queue is QueueLevel.BUSY


def test_espera_curta_e_normal() -> None:
    status = classify(reading(1, SLOW_AFTER - timedelta(seconds=1)), NOW)

    assert status.processing is ProcessingLevel.NORMAL


def test_espera_acima_de_2_min_e_lenta() -> None:
    status = classify(reading(1, timedelta(minutes=3)), NOW)

    assert status.processing is ProcessingLevel.SLOW


def test_espera_acima_de_10_min_sem_lease_vivo_e_parada() -> None:
    status = classify(reading(1, STALLED_AFTER + timedelta(minutes=1)), NOW)

    assert status.processing is ProcessingLevel.STALLED


def test_espera_acima_de_10_min_com_lease_vivo_e_lenta() -> None:
    status = classify(reading(1, STALLED_AFTER + timedelta(minutes=1), lease_alive=True), NOW)

    assert status.processing is ProcessingLevel.SLOW


def test_elegivel_no_futuro_e_normal() -> None:
    # Retentativa com recuo: `eligible_at` ainda não chegou e a espera fica negativa.
    status = classify(reading(1, timedelta(seconds=-30)), NOW)

    assert status.processing is ProcessingLevel.NORMAL


# --- Leitura no banco (PostgreSQL real) -------------------------------------------------------


def queue_run(factory: sessionmaker[Session], waiting: timedelta) -> uuid.UUID:
    """Execução em fila há `waiting`, pelo relógio do banco; uma por conta (1.7)."""
    with unit_of_work(factory) as session:
        run_id: uuid.UUID = session.execute(
            insert(run)
            .values(
                owner_id=new_owner(factory),
                recipe_id="csv-janitor",
                recipe_version=1,
                executor_digest="local-dev",
                parameters={},
                limits={},
                eligible_at=func.now() - waiting,
                created_at=func.now() - waiting,
                queue_expires_at=func.now() + timedelta(hours=1),
            )
            .returning(run.c.id)
        ).scalar_one()
    return run_id


def running_run(factory: sessionmaker[Session], lease_in: timedelta) -> None:
    """Execução em andamento com tentativa viva cujo lease vence daqui a `lease_in`."""
    with unit_of_work(factory) as session:
        run_id = session.execute(
            insert(run)
            .values(
                owner_id=new_owner(factory),
                recipe_id="csv-janitor",
                recipe_version=1,
                executor_digest="local-dev",
                parameters={},
                limits={},
                status=Status.RUNNING,
                phase=Phase.PROCESSING,
                generation=1,
                claim_count=1,
                started_at=func.now(),
                queue_expires_at=func.now() + timedelta(hours=1),
            )
            .returning(run.c.id)
        ).scalar_one()
        session.execute(
            insert(attempt).values(
                run_id=run_id,
                number=1,
                generation=1,
                container_name=f"gs-{uuid.uuid4()}",
                lease_expires_at=func.now() + lease_in,
            )
        )


def read(factory: sessionmaker[Session]) -> ServiceStatus:
    with factory() as session, session.begin():
        return read_service_status(session)


@pytest.mark.integration
def test_banco_com_fila_vazia_e_normal_e_livre(factory: sessionmaker[Session]) -> None:
    status = read(factory)

    assert (status.processing, status.queue) == (ProcessingLevel.NORMAL, QueueLevel.FREE)


@pytest.mark.integration
def test_banco_com_10_em_fila_e_cheia(factory: sessionmaker[Session]) -> None:
    for _ in range(MAX_QUEUED_RUNS):
        queue_run(factory, timedelta(seconds=5))

    assert read(factory).queue is QueueLevel.FULL


@pytest.mark.integration
def test_banco_com_fila_parada_ha_11_min_sem_lease_vivo_e_parada(
    factory: sessionmaker[Session],
) -> None:
    queue_run(factory, timedelta(minutes=11))
    # Tentativa de outra execução com o lease vencido: não prova que o worker está de pé.
    running_run(factory, timedelta(minutes=-1))

    status = read(factory)

    assert (status.processing, status.queue) == (ProcessingLevel.STALLED, QueueLevel.BUSY)


@pytest.mark.integration
def test_banco_com_fila_ha_11_min_e_lease_vivo_e_lenta(factory: sessionmaker[Session]) -> None:
    queue_run(factory, timedelta(minutes=11))
    running_run(factory, timedelta(minutes=1))

    assert read(factory).processing is ProcessingLevel.SLOW


@pytest.mark.integration
def test_banco_com_fila_ha_3_min_e_lenta(factory: sessionmaker[Session]) -> None:
    queue_run(factory, timedelta(minutes=3))

    assert read(factory).processing is ProcessingLevel.SLOW


@pytest.mark.integration
def test_banco_le_a_hora_do_proprio_banco(factory: sessionmaker[Session]) -> None:
    before = datetime.now(UTC)

    checked_at = read(factory).checked_at

    assert checked_at.tzinfo is not None
    assert abs(checked_at - before) < timedelta(minutes=1)
