"""Retenção do histórico de execuções (docs/GoblinShiftPlan.md, 1.7, "Retenção").

Parâmetros, códigos e detalhes por item (`run_input.details`) anulados 30 dias depois do
término, Run e Attempt removidas aos 90, `IdempotencyRecord` vencido removido. Cada função recebe
o instante e o teto do lote, então as fronteiras são testadas sem relógio real: `on point` no
prazo exato e `off point` 1 s antes.
"""

import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import func, insert, select, update
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.files.artifacts import create_candidate_object
from goblinshift.files.storage import FileStorage
from goblinshift.platform.db import unit_of_work
from goblinshift.runs.retention import (
    ANONYMIZE_AFTER,
    DELETE_AFTER,
    anonymize_finished_runs,
    delete_expired_idempotency_records,
    delete_old_runs,
    history_backlog,
)
from goblinshift.runs.store import db_now
from goblinshift.runs.tables import attempt, idempotency_record, run, run_input
from tests.runs.support import insert_finished_run, new_owner, ready_inputs

pytestmark = pytest.mark.integration

SECOND = timedelta(seconds=1)
LIMIT = 10
REJECTED_DETAILS = {"warnings": ["ENCODING_GUESSED"], "rows_read": 3}
ACCEPTED_DETAILS = {"warnings": [], "rows_read": 7, "rows_written": 7}


def now_of(factory: sessionmaker[Session]) -> datetime:
    with factory() as session:
        return db_now(session)


def finished_run(
    factory: sessionmaker[Session],
    storage: FileStorage,
    finished_at: datetime,
    owner: uuid.UUID | None = None,
) -> uuid.UUID:
    """Run terminada em `finished_at`, com uma entrada rejeitada e outra aceita, ambas com
    detalhe por item, uma tentativa e um registro de idempotência."""
    owner = owner or new_owner(factory)
    upload_id, accepted_id = ready_inputs(factory, storage, owner, count=2)
    run_id = insert_finished_run(factory, owner, finished_at - timedelta(minutes=1))
    with unit_of_work(factory) as session:
        session.execute(
            update(run)
            .where(run.c.id == run_id)
            .values(parameters={"filters": [{"column": "cpf"}]}, finished_at=finished_at)
        )
        session.execute(
            insert(run_input).values(
                run_id=run_id,
                position=0,
                owner_id=owner,
                upload_id=upload_id,
                outcome="rejected",
                error_code="CSV_STRUCTURE_INVALID",
                details=REJECTED_DETAILS,
            )
        )
        # Aceita: sem código, mas com detalhe; a anulação não pode depender do código.
        session.execute(
            insert(run_input).values(
                run_id=run_id,
                position=1,
                owner_id=owner,
                upload_id=accepted_id,
                outcome="ok",
                details=ACCEPTED_DETAILS,
            )
        )
        session.execute(
            insert(attempt).values(
                run_id=run_id,
                number=1,
                generation=1,
                container_name=f"gs-{uuid.uuid4()}",
                lease_expires_at=finished_at,
                ended_at=finished_at,
            )
        )
        session.execute(
            insert(idempotency_record).values(
                owner_id=owner,
                operation="create_run",
                idem_key=uuid.uuid4().hex,
                command_hash=b"\x00" * 32,
                run_id=run_id,
                expires_at=finished_at + timedelta(days=1),
            )
        )
    return run_id


def run_details(factory: sessionmaker[Session], run_id: uuid.UUID) -> tuple[object, ...]:
    """Parâmetros da Run e, por entrada em ordem, (desfecho, código, detalhe é NULL de SQL?)."""
    with factory() as session:
        parameters = session.execute(
            select(run.c.parameters).where(run.c.id == run_id)
        ).scalar_one()
        inputs = session.execute(
            select(run_input.c.outcome, run_input.c.error_code, run_input.c.details)
            .where(run_input.c.run_id == run_id)
            .order_by(run_input.c.position)
        ).all()
    return parameters, [tuple(row) for row in inputs]


def exists(factory: sessionmaker[Session], run_id: uuid.UUID) -> bool:
    with factory() as session:
        return session.execute(select(run.c.id).where(run.c.id == run_id)).first() is not None


def count(factory: sessionmaker[Session], table_name: str) -> int:
    tables = {"attempt": attempt, "run_input": run_input, "idempotency": idempotency_record}
    with factory() as session:
        return session.execute(select(func.count()).select_from(tables[table_name])).scalar_one()


# --- Anulação aos 30 dias ---------------------------------------------------------------------


def test_run_terminada_ha_30_dias_perde_parametros_codigo_e_detalhe_por_item(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    now = now_of(factory)
    run_id = finished_run(factory, storage, now - ANONYMIZE_AFTER)

    with unit_of_work(factory) as session:
        anonymized = anonymize_finished_runs(session, now, LIMIT)

    assert anonymized == 1
    # O desfecho (ok/rejected) fica: só diz quantas entradas foram processadas, sem detalhe.
    assert run_details(factory, run_id) == (None, [("rejected", None, None), ("ok", None, None)])
    with factory() as session:
        sql_nulls = session.execute(
            select(
                run.c.parameters.is_(None),
                select(func.bool_and(run_input.c.details.is_(None)))
                .where(run_input.c.run_id == run_id)
                .scalar_subquery(),
            ).where(run.c.id == run_id)
        ).one()
    assert tuple(sql_nulls) == (True, True)  # NULL de SQL, não o JSON `null`
    assert exists(factory, run_id)


def test_run_terminada_1_s_antes_dos_30_dias_fica_intacta(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    now = now_of(factory)
    run_id = finished_run(factory, storage, now - ANONYMIZE_AFTER + SECOND)

    with unit_of_work(factory) as session:
        anonymized = anonymize_finished_runs(session, now, LIMIT)

    assert anonymized == 0
    assert run_details(factory, run_id) == (
        {"filters": [{"column": "cpf"}]},
        [
            ("rejected", "CSV_STRUCTURE_INVALID", REJECTED_DETAILS),
            ("ok", None, ACCEPTED_DETAILS),
        ],
    )


def test_anulacao_respeita_o_lote_e_repetir_nao_faz_nada(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    now = now_of(factory)
    for days in (31, 32, 33):
        finished_run(factory, storage, now - timedelta(days=days))

    with unit_of_work(factory) as session:
        first = anonymize_finished_runs(session, now, 2)
    with unit_of_work(factory) as session:
        second = anonymize_finished_runs(session, now, 2)
    with unit_of_work(factory) as session:
        third = anonymize_finished_runs(session, now, 2)

    assert (first, second, third) == (2, 1, 0)


# --- Remoção aos 90 dias -----------------------------------------------------------------------


def test_run_terminada_ha_90_dias_sai_com_tentativas_entradas_e_idempotencia(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    now = now_of(factory)
    old = finished_run(factory, storage, now - DELETE_AFTER)
    recent = finished_run(factory, storage, now - DELETE_AFTER + SECOND)

    with unit_of_work(factory) as session:
        deleted = delete_old_runs(session, now, LIMIT)

    assert deleted == 1
    assert not exists(factory, old)
    assert exists(factory, recent)
    assert (count(factory, "attempt"), count(factory, "run_input")) == (1, 2)
    assert count(factory, "idempotency") == 1


def test_run_ativa_nunca_e_removida(factory: sessionmaker[Session], storage: FileStorage) -> None:
    now = now_of(factory)
    owner = new_owner(factory)
    run_id = insert_finished_run(factory, owner, now - timedelta(days=200))
    with unit_of_work(factory) as session:
        session.execute(
            update(run)
            .where(run.c.id == run_id)
            .values(status="queued", error_code=None, finished_at=None)
        )

    with unit_of_work(factory) as session:
        deleted = delete_old_runs(session, now, LIMIT)

    assert deleted == 0
    assert exists(factory, run_id)


def test_run_com_candidato_ainda_no_disco_espera_o_descarte(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    """Apagar a tentativa antes do descarte deixaria o arquivo do candidato sem quem o remova."""
    now = now_of(factory)
    run_id = finished_run(factory, storage, now - DELETE_AFTER - timedelta(days=1))
    with unit_of_work(factory) as session:
        attempt_id, owner = session.execute(
            select(attempt.c.id, run.c.owner_id)
            .join(run, run.c.id == attempt.c.run_id)
            .where(run.c.id == run_id)
        ).one()
        create_candidate_object(session, owner, run_id, attempt_id, 1)

    with unit_of_work(factory) as session:
        deleted = delete_old_runs(session, now, LIMIT)

    assert deleted == 0
    assert exists(factory, run_id)


def test_remocao_respeita_o_lote(factory: sessionmaker[Session], storage: FileStorage) -> None:
    now = now_of(factory)
    for days in (91, 92, 93):
        finished_run(factory, storage, now - timedelta(days=days))

    with unit_of_work(factory) as session:
        first = delete_old_runs(session, now, 2)
    with unit_of_work(factory) as session:
        second = delete_old_runs(session, now, 2)

    assert (first, second) == (2, 1)


# --- IdempotencyRecord ------------------------------------------------------------------------


def test_registro_de_idempotencia_vencido_sai_e_o_vigente_fica(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    now = now_of(factory)
    finished_run(factory, storage, now - timedelta(days=1))  # vence exatamente agora
    finished_run(factory, storage, now - timedelta(days=1) + SECOND)  # vence em 1 s

    with unit_of_work(factory) as session:
        removed = delete_expired_idempotency_records(session, now, LIMIT)

    assert removed == 1
    assert count(factory, "idempotency") == 1


# --- Tamanho do acúmulo -----------------------------------------------------------------------


def test_backlog_conta_o_que_ainda_falta_em_cada_retencao(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    now = now_of(factory)
    finished_run(factory, storage, now - timedelta(days=100))  # anular, remover e idempotência
    finished_run(factory, storage, now - timedelta(days=40))  # anular e idempotência
    finished_run(factory, storage, now - timedelta(hours=1))  # nada

    with factory() as session:
        backlog = history_backlog(session, now)

    assert backlog == {
        "runs_to_anonymize": 2,
        "runs_to_delete": 1,
        "idempotency_records_expired": 2,
    }
