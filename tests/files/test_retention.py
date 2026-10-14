"""Expiração em lote e remoção física com limitador (1.7 e 3.4)."""

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import Table, func, insert, select, update
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.files.artifacts import open_for_download, publish_artifact
from goblinshift.files.errors import UploadNotFoundError
from goblinshift.files.objects import hold, release
from goblinshift.files.retention import (
    expire_artifacts,
    expire_incomplete_uploads,
    expire_unused_uploads,
    purge_deleted_objects,
)
from goblinshift.files.states import ArtifactRole, HolderKind
from goblinshift.files.storage import FileStorage, ObjectMissingError
from goblinshift.files.tables import artifact, stored_object, upload
from goblinshift.files.uploads import (
    UploadView,
    get_upload,
    lock_ready_uploads,
    request_deletion,
)
from goblinshift.platform.db import unit_of_work
from tests.files.support import create_intent, create_user, ready_upload

pytestmark = pytest.mark.integration

GIB = 1024 * 1024 * 1024


def expire_row(factory: sessionmaker[Session], table: Table, row_id: uuid.UUID) -> None:
    with unit_of_work(factory) as session:
        session.execute(
            update(table)
            .where(table.c.id == row_id)
            .values(expires_at=func.now() - timedelta(seconds=1))
        )


def object_of(factory: sessionmaker[Session], upload_id: uuid.UUID) -> tuple[str, str]:
    with factory() as session:
        row = session.execute(
            select(stored_object.c.state, stored_object.c.storage_key)
            .join(upload, upload.c.object_id == stored_object.c.id)
            .where(upload.c.id == upload_id)
        ).one()
    return row.state, row.storage_key


def object_states(factory: sessionmaker[Session]) -> dict[str, int]:
    with factory() as session:
        rows = session.execute(
            select(stored_object.c.state, func.count()).group_by(stored_object.c.state)
        ).all()
    return {state: count for state, count in rows}


def seed_delete_requested(
    factory: sessionmaker[Session], owner: uuid.UUID, count: int, size_bytes: int
) -> None:
    """Objetos baratos já pedidos para exclusão, só com a linha (remover arquivo ausente é ok)."""
    with unit_of_work(factory) as session:
        session.execute(
            insert(stored_object).values(
                state="delete_requested",
                sha256=b"\x00" * 32,
                delete_requested_at=func.now(),
            ),
            [
                {"owner_id": owner, "storage_key": f"seed/{i}", "size_bytes": size_bytes}
                for i in range(count)
            ],
        )


def ready_in_use(
    factory: sessionmaker[Session], storage: FileStorage, owner: uuid.UUID, run_id: uuid.UUID
) -> UploadView:
    view = ready_upload(factory, storage, owner)
    with unit_of_work(factory) as session:
        [ready] = lock_ready_uploads(session, owner, [view.id])
        hold(session, [ready.object_id], HolderKind.RUN, run_id)
    return view


# --- Uploads incompletos ----------------------------------------------------------------------


def test_upload_incompleto_vencido_perde_linha_e_objeto(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = create_user(factory, "a")
    stale = create_intent(factory, owner)
    fresh = create_intent(factory, owner)
    expire_row(factory, upload, stale.id)

    with factory() as session:
        assert expire_incomplete_uploads(session, storage) == 1

    with pytest.raises(UploadNotFoundError), factory() as session:
        get_upload(session, owner, stale.id)
    with factory() as session:
        assert get_upload(session, owner, fresh.id).deleted_at is None
    assert object_states(factory) == {"pending": 1}


def test_upload_incompleto_excluido_sai_sem_esperar_o_prazo(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = create_user(factory, "a")
    intent = create_intent(factory, owner)
    with unit_of_work(factory) as session:
        request_deletion(session, owner, intent.id)

    with factory() as session:
        assert expire_incomplete_uploads(session, storage) == 1

    assert object_states(factory) == {}


# --- Uploads prontos sem uso ------------------------------------------------------------------


def test_upload_pronto_vencido_sem_uso_e_excluido(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = create_user(factory, "a")
    view = ready_upload(factory, storage, owner)
    expire_row(factory, upload, view.id)

    with factory() as session:
        assert expire_unused_uploads(session) == 1

    with factory() as session:
        assert get_upload(session, owner, view.id).deleted_at is not None
    assert object_of(factory, view.id)[0] == "delete_requested"


def test_upload_pronto_vencido_em_uso_continua_valido(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = create_user(factory, "a")
    view = ready_in_use(factory, storage, owner, uuid.uuid4())
    expire_row(factory, upload, view.id)

    with factory() as session:
        assert expire_unused_uploads(session) == 0

    assert object_of(factory, view.id)[0] == "committed"


def test_upload_pronto_dentro_do_prazo_nao_expira(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = create_user(factory, "a")
    ready_upload(factory, storage, owner)

    with factory() as session:
        assert expire_unused_uploads(session) == 0


# --- Artefatos --------------------------------------------------------------------------------


def test_artefato_vencido_e_excluido_e_objeto_pedido_para_remocao(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = create_user(factory, "a")
    view = ready_upload(factory, storage, owner)
    with unit_of_work(factory) as session:
        [ready] = lock_ready_uploads(session, owner, [view.id])
        shared = publish_artifact(session, owner, ready.object_id, ArtifactRole.OUTPUT)
        request_deletion(session, owner, view.id)
    assert object_of(factory, view.id)[0] == "committed"
    expire_row(factory, artifact, shared.id)

    with factory() as session:
        assert expire_artifacts(session) == 1

    assert object_of(factory, view.id)[0] == "delete_requested"


def test_artefato_vencido_que_divide_objeto_com_upload_valido_preserva_o_objeto(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = create_user(factory, "a")
    view = ready_upload(factory, storage, owner)
    with unit_of_work(factory) as session:
        [ready] = lock_ready_uploads(session, owner, [view.id])
        shared = publish_artifact(session, owner, ready.object_id, ArtifactRole.OUTPUT)
    expire_row(factory, artifact, shared.id)

    with factory() as session:
        assert expire_artifacts(session) == 1

    assert object_of(factory, view.id)[0] == "committed"


# --- Remoção física ---------------------------------------------------------------------------


def test_exclusao_com_uso_aberto_so_remove_o_arquivo_depois_do_release(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = create_user(factory, "a")
    run_id = uuid.uuid4()
    view = ready_in_use(factory, storage, owner, run_id)
    with unit_of_work(factory) as session:
        request_deletion(session, owner, view.id)
    _, key = object_of(factory, view.id)

    with factory() as session:
        assert purge_deleted_objects(session, storage).objects == 0
    assert storage.stat(key).size_bytes == 3

    with unit_of_work(factory) as session:
        release(session, HolderKind.RUN, run_id)
    with factory() as session:
        report = purge_deleted_objects(session, storage)

    assert (report.objects, report.bytes, report.limit_reached) == (1, 3, False)
    assert object_of(factory, view.id)[0] == "deleted"
    with pytest.raises(ObjectMissingError):
        storage.stat(key)


def test_download_em_andamento_segura_a_remocao_do_artefato_excluido(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = create_user(factory, "a")
    view = ready_upload(factory, storage, owner)
    with unit_of_work(factory) as session:
        [ready] = lock_ready_uploads(session, owner, [view.id])
        shared = publish_artifact(session, owner, ready.object_id, ArtifactRole.OUTPUT)
        request_deletion(session, owner, view.id)
    with unit_of_work(factory) as session:
        ticket = open_for_download(session, owner, shared.id)
    expire_row(factory, artifact, shared.id)
    with factory() as session:
        expire_artifacts(session)

    with factory() as session:
        assert purge_deleted_objects(session, storage).objects == 0
    with storage.open(ticket.storage_key) as stream:
        assert stream.read() == b"abc"


def test_remocao_para_em_500_objetos_por_ciclo(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = create_user(factory, "a")
    seed_delete_requested(factory, owner, count=501, size_bytes=1)

    with factory() as session:
        first = purge_deleted_objects(session, storage)
    with factory() as session:
        second = purge_deleted_objects(session, storage)

    assert (first.objects, first.limit_reached) == (500, True)
    assert (second.objects, second.limit_reached) == (1, False)
    assert object_states(factory) == {"deleted": 501}


def test_remocao_para_em_2_gib_por_ciclo(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = create_user(factory, "a")
    seed_delete_requested(factory, owner, count=3, size_bytes=GIB)

    with factory() as session:
        report = purge_deleted_objects(session, storage)

    assert (report.objects, report.bytes, report.limit_reached) == (2, 2 * GIB, True)
    assert object_states(factory) == {"deleted": 2, "delete_requested": 1}


def test_remocao_sem_candidatos_nao_faz_nada(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    with factory() as session:
        report = purge_deleted_objects(session, storage)

    assert (report.objects, report.bytes, report.limit_reached) == (0, 0, False)
