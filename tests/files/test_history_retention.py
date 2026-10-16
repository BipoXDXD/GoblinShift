"""Retenção longa de `files` (docs/GoblinShiftPlan.md, 1.7, "Retenção").

O nome do upload é anulado 30 dias depois da criação, só depois da exclusão e sem uso aberto
(uma execução ainda lê o nome ao montar a área de trabalho). As linhas de upload, artefato e
objeto saem aos 90 dias, só quando o arquivo já saiu do disco e nada do histórico as referencia.
Usos encerrados e reservas liberadas também têm limpeza: nada acumula sem reciclagem.
"""

import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import Table, func, select, update
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.files.objects import hold, release
from goblinshift.files.retention import (
    FILE_ROWS_AFTER,
    NAME_AFTER,
    anonymize_deleted_uploads,
    delete_old_file_rows,
    file_backlog,
)
from goblinshift.files.states import HolderKind, ObjectState
from goblinshift.files.storage import FileStorage
from goblinshift.files.tables import object_use, stored_object, upload
from goblinshift.files.uploads import request_deletion
from goblinshift.platform.db import unit_of_work
from goblinshift.runs.retention import HISTORY_REFERENCES
from goblinshift.runs.store import db_now
from tests.files.support import create_user, ready_upload

pytestmark = pytest.mark.integration

SECOND = timedelta(seconds=1)
LIMIT = 10


def now_of(factory: sessionmaker[Session]) -> datetime:
    with factory() as session:
        return db_now(session)


def deleted_upload(
    factory: sessionmaker[Session],
    storage: FileStorage,
    created_at: datetime,
    *,
    object_state: ObjectState = ObjectState.DELETED,
) -> uuid.UUID:
    owner = create_user(factory, f"conta-{uuid.uuid4()}")
    view = ready_upload(factory, storage, owner)
    with unit_of_work(factory) as session:
        request_deletion(session, owner, view.id)
        session.execute(update(upload).where(upload.c.id == view.id).values(created_at=created_at))
        object_id = session.execute(
            select(upload.c.object_id).where(upload.c.id == view.id)
        ).scalar_one()
        session.execute(
            update(stored_object)
            .where(stored_object.c.id == object_id)
            .values(state=object_state, created_at=created_at)
        )
    return view.id


def name_of(factory: sessionmaker[Session], upload_id: uuid.UUID) -> str | None:
    with factory() as session:
        name: str | None = session.execute(
            select(upload.c.original_name).where(upload.c.id == upload_id)
        ).scalar_one()
    return name


def rows(factory: sessionmaker[Session], table: Table) -> int:
    with factory() as session:
        return session.execute(select(func.count()).select_from(table)).scalar_one()


# --- Nome do upload aos 30 dias ----------------------------------------------------------------


def test_upload_excluido_criado_ha_30_dias_perde_o_nome(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    now = now_of(factory)
    old = deleted_upload(factory, storage, now - NAME_AFTER)
    recent = deleted_upload(factory, storage, now - NAME_AFTER + SECOND)

    with unit_of_work(factory) as session:
        anonymized = anonymize_deleted_uploads(session, now, LIMIT)

    assert anonymized == 1
    assert name_of(factory, old) is None
    assert name_of(factory, recent) == "a.txt"


def test_upload_vivo_guarda_o_nome_mesmo_depois_de_30_dias(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    """Reaproveitado por execuções seguidas, ele ainda é lido; o nome sai depois da exclusão."""
    now = now_of(factory)
    owner = create_user(factory, "ana")
    view = ready_upload(factory, storage, owner)
    with unit_of_work(factory) as session:
        session.execute(
            update(upload).where(upload.c.id == view.id).values(created_at=now - timedelta(days=60))
        )

    with unit_of_work(factory) as session:
        anonymized = anonymize_deleted_uploads(session, now, LIMIT)

    assert anonymized == 0
    assert name_of(factory, view.id) == "a.txt"


def test_upload_excluido_ainda_em_uso_guarda_o_nome(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    now = now_of(factory)
    upload_id = deleted_upload(
        factory, storage, now - timedelta(days=60), object_state=ObjectState.COMMITTED
    )
    with unit_of_work(factory) as session:
        object_id = session.execute(
            select(upload.c.object_id).where(upload.c.id == upload_id)
        ).scalar_one()
        hold(session, [object_id], HolderKind.RUN, uuid.uuid7())

    with unit_of_work(factory) as session:
        anonymized = anonymize_deleted_uploads(session, now, LIMIT)

    assert anonymized == 0
    assert name_of(factory, upload_id) == "a.txt"


def test_anulacao_de_nomes_respeita_o_lote_e_e_idempotente(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    now = now_of(factory)
    for days in (31, 32, 33):
        deleted_upload(factory, storage, now - timedelta(days=days))

    results = []
    for _ in range(3):
        with unit_of_work(factory) as session:
            results.append(anonymize_deleted_uploads(session, now, 2))

    assert results == [2, 1, 0]


# --- Linhas aos 90 dias ------------------------------------------------------------------------


def test_linhas_de_upload_sem_historico_saem_aos_90_dias_com_o_objeto(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    now = now_of(factory)
    deleted_upload(factory, storage, now - FILE_ROWS_AFTER)
    kept = deleted_upload(factory, storage, now - FILE_ROWS_AFTER + SECOND)

    with unit_of_work(factory) as session:
        removed = delete_old_file_rows(session, now, LIMIT, HISTORY_REFERENCES)

    assert removed == 1
    assert rows(factory, upload) == 1
    assert rows(factory, stored_object) == 1
    assert name_of(factory, kept) == "a.txt"


@pytest.mark.parametrize("state", [ObjectState.COMMITTED, ObjectState.DELETE_REQUESTED])
def test_linha_fica_enquanto_o_arquivo_pode_estar_no_disco(
    factory: sessionmaker[Session], storage: FileStorage, state: ObjectState
) -> None:
    now = now_of(factory)
    deleted_upload(factory, storage, now - timedelta(days=200), object_state=state)

    with unit_of_work(factory) as session:
        removed = delete_old_file_rows(session, now, LIMIT, HISTORY_REFERENCES)

    assert removed == 0
    assert rows(factory, upload) == 1


def test_objeto_missing_conta_como_fora_do_disco(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    now = now_of(factory)
    deleted_upload(factory, storage, now - timedelta(days=200), object_state=ObjectState.MISSING)

    with unit_of_work(factory) as session:
        removed = delete_old_file_rows(session, now, LIMIT, HISTORY_REFERENCES)

    assert removed == 1
    assert rows(factory, stored_object) == 0


def test_usos_encerrados_ha_90_dias_saem_e_o_aberto_fica(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    now = now_of(factory)
    view = ready_upload(factory, storage, create_user(factory, "ana"))
    old, recent, live = uuid.uuid7(), uuid.uuid7(), uuid.uuid7()
    with unit_of_work(factory) as session:
        object_id = session.execute(
            select(upload.c.object_id).where(upload.c.id == view.id)
        ).scalar_one()
        for holder in (old, recent, live):
            hold(session, [object_id], HolderKind.DOWNLOAD, holder)
        release(session, HolderKind.DOWNLOAD, old)
        release(session, HolderKind.DOWNLOAD, recent)
        session.execute(
            update(object_use)
            .where(object_use.c.holder_id == old)
            .values(ended_at=now - FILE_ROWS_AFTER)
        )
        session.execute(
            update(object_use)
            .where(object_use.c.holder_id == recent)
            .values(ended_at=now - FILE_ROWS_AFTER + SECOND)
        )

    with unit_of_work(factory) as session:
        delete_old_file_rows(session, now, LIMIT, HISTORY_REFERENCES)

    with factory() as session:
        holders = set(session.execute(select(object_use.c.holder_id)).scalars())
    assert holders == {recent, live}


def test_linhas_respeitam_o_lote(factory: sessionmaker[Session], storage: FileStorage) -> None:
    now = now_of(factory)
    for days in (91, 92, 93):
        deleted_upload(factory, storage, now - timedelta(days=days))

    with unit_of_work(factory) as session:
        first = delete_old_file_rows(session, now, 2, HISTORY_REFERENCES)
    with unit_of_work(factory) as session:
        second = delete_old_file_rows(session, now, 2, HISTORY_REFERENCES)

    assert (first, second) == (2, 1)


def test_backlog_de_files(factory: sessionmaker[Session], storage: FileStorage) -> None:
    now = now_of(factory)
    deleted_upload(factory, storage, now - timedelta(days=100))  # nome e linhas
    deleted_upload(factory, storage, now - timedelta(days=40))  # só o nome
    deleted_upload(factory, storage, now - timedelta(days=1))  # nada

    with factory() as session:
        backlog = file_backlog(session, now)

    assert backlog == {"uploads_to_anonymize": 2, "uploads_to_delete": 1}
