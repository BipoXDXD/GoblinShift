"""Retenção longa de `files` (docs/GoblinShiftPlan.md, 1.7, "Retenção").

O nome do upload é anulado 30 dias depois da criação, só depois da exclusão e sem uso aberto
(uma execução ainda lê o nome ao montar a área de trabalho). As linhas de upload, artefato e
objeto saem aos 90 dias, só quando o arquivo já saiu do disco e nada do histórico as referencia.
Usos encerrados e reservas liberadas também têm limpeza: nada acumula sem reciclagem.
"""

import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import Table, func, insert, select, update
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.files.limits import UPLOAD_REQUEST_KEY_TTL
from goblinshift.files.objects import hold, release
from goblinshift.files.retention import (
    FILE_ROWS_AFTER,
    NAME_AFTER,
    anonymize_deleted_uploads,
    delete_old_file_rows,
    file_backlog,
    forget_upload_request_keys,
)
from goblinshift.files.states import ArtifactRole, HolderKind, ObjectState
from goblinshift.files.storage import FileStorage
from goblinshift.files.tables import artifact, object_use, stored_object, upload
from goblinshift.files.upload_requests import create_intents_once
from goblinshift.files.uploads import request_deletion
from goblinshift.platform.db import unit_of_work
from goblinshift.runs.retention import HISTORY_REFERENCES
from goblinshift.runs.store import db_now
from tests.files.support import create_intent, create_user, ready_upload

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


# --- Chave de idempotência do lote às 24 h -----------------------------------------------------


def keyed_batch(factory: sessionmaker[Session], created_at: datetime, size: int = 1) -> uuid.UUID:
    """Lote criado com Idempotency-Key, recuado para `created_at`; devolve o dono."""
    owner = create_user(factory, f"conta-{uuid.uuid4()}")
    items = [(f"f{i}.txt", 1) for i in range(size)]
    with unit_of_work(factory) as session:
        create_intents_once(session, owner, items, "k" * 16)
        session.execute(
            update(upload).where(upload.c.owner_id == owner).values(created_at=created_at)
        )
    return owner


def request_columns_of(factory: sessionmaker[Session], owner: uuid.UUID) -> set[tuple[object, ...]]:
    with factory() as session:
        return {
            tuple(row)
            for row in session.execute(
                select(upload.c.request_key, upload.c.request_hash, upload.c.request_index).where(
                    upload.c.owner_id == owner
                )
            )
        }


def test_chave_do_lote_criado_ha_24_horas_e_anulada(
    factory: sessionmaker[Session],
) -> None:
    now = now_of(factory)
    old = keyed_batch(factory, now - UPLOAD_REQUEST_KEY_TTL, size=2)
    recent = keyed_batch(factory, now - UPLOAD_REQUEST_KEY_TTL + SECOND)

    with unit_of_work(factory) as session:
        forgotten = forget_upload_request_keys(session, now, LIMIT)

    assert forgotten == 2
    assert request_columns_of(factory, old) == {(None, None, None)}
    assert request_columns_of(factory, recent) != {(None, None, None)}


def test_anulacao_de_chaves_respeita_o_lote(factory: sessionmaker[Session]) -> None:
    now = now_of(factory)
    for hours in (25, 26, 27):
        keyed_batch(factory, now - timedelta(hours=hours))

    results = []
    for _ in range(3):
        with unit_of_work(factory) as session:
            results.append(forget_upload_request_keys(session, now, 2))

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


def test_usos_encerrados_saem_no_teto_do_lote(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    now = now_of(factory)
    view = ready_upload(factory, storage, create_user(factory, "ana"))
    holders = [uuid.uuid7() for _ in range(3)]
    with unit_of_work(factory) as session:
        object_id = session.execute(
            select(upload.c.object_id).where(upload.c.id == view.id)
        ).scalar_one()
        for holder in holders:
            hold(session, [object_id], HolderKind.DOWNLOAD, holder)
            release(session, HolderKind.DOWNLOAD, holder)
        session.execute(update(object_use).values(ended_at=now - FILE_ROWS_AFTER))

    with unit_of_work(factory) as session:
        delete_old_file_rows(session, now, 2, HISTORY_REFERENCES)

    assert rows(factory, object_use) == 1


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

    assert backlog == {
        "uploads_to_anonymize": 2,
        "uploads_to_delete": 1,
        "incomplete_uploads_to_expire": 0,
        "artifacts_to_expire": 0,
        "objects_to_purge": 0,
    }


def incomplete_upload(factory: sessionmaker[Session], expires_at: datetime) -> None:
    view = create_intent(factory, create_user(factory, f"conta-{uuid.uuid4()}"))
    with unit_of_work(factory) as session:
        session.execute(update(upload).where(upload.c.id == view.id).values(expires_at=expires_at))


def stored(factory: sessionmaker[Session], owner: uuid.UUID, state: ObjectState) -> uuid.UUID:
    """Objeto com conteúdo conhecido, inserido direto: só a linha importa para a contagem."""
    with unit_of_work(factory) as session:
        object_id: uuid.UUID = session.execute(
            insert(stored_object)
            .values(
                owner_id=owner,
                storage_key=f"objects/{uuid.uuid4()}",
                state=state,
                sha256=bytes(32),
                size_bytes=3,
            )
            .returning(stored_object.c.id)
        ).scalar_one()
    return object_id


def published(
    factory: sessionmaker[Session], expires_at: datetime, *, deleted: bool = False
) -> None:
    owner = create_user(factory, f"conta-{uuid.uuid4()}")
    object_id = stored(factory, owner, ObjectState.COMMITTED)
    with unit_of_work(factory) as session:
        session.execute(
            insert(artifact).values(
                owner_id=owner,
                object_id=object_id,
                role=ArtifactRole.OUTPUT,
                file_name="saida.csv",
                expires_at=expires_at,
                deleted_at=func.now() if deleted else None,
            )
        )


def test_backlog_de_files_conta_vencidos_e_pedidos_de_exclusao(
    factory: sessionmaker[Session],
) -> None:
    now = now_of(factory)
    incomplete_upload(factory, now)  # vence exatamente agora: conta
    incomplete_upload(factory, now + SECOND)  # ainda no prazo
    published(factory, now)  # vence exatamente agora: conta
    published(factory, now + SECOND)  # ainda no prazo
    published(factory, now - SECOND, deleted=True)  # já expirado pela retenção
    owner = create_user(factory, f"conta-{uuid.uuid4()}")
    stored(factory, owner, ObjectState.DELETE_REQUESTED)
    stored(factory, owner, ObjectState.DELETED)

    with factory() as session:
        backlog = file_backlog(session, now)

    assert backlog == {
        "uploads_to_anonymize": 0,
        "uploads_to_delete": 0,
        "incomplete_uploads_to_expire": 1,
        "artifacts_to_expire": 1,
        "objects_to_purge": 1,
    }


def test_incompleto_cancelado_antes_do_prazo_entra_no_acumulo(
    factory: sessionmaker[Session],
) -> None:
    """A expiração também leva o incompleto que o dono excluiu, mesmo dentro do prazo."""
    now = now_of(factory)
    view = create_intent(factory, create_user(factory, f"conta-{uuid.uuid4()}"))
    with unit_of_work(factory) as session:
        session.execute(
            update(upload)
            .where(upload.c.id == view.id)
            .values(expires_at=now + timedelta(hours=1), deleted_at=now)
        )

    with factory() as session:
        backlog = file_backlog(session, now)

    assert backlog["incomplete_uploads_to_expire"] == 1
