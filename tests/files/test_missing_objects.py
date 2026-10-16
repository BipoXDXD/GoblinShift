"""Objetos ausentes do storage viram `missing` (1.7, 3.3 e 3.8; ADR 007 e 009)."""

import uuid

import pytest
from sqlalchemy import insert, select
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.capacity import HolderKind as CapacityHolder
from goblinshift.files.artifacts import get_artifact, publish_artifact
from goblinshift.files.missing import mark_missing, sweep_missing_objects
from goblinshift.files.objects import hold
from goblinshift.files.retention import purge_deleted_objects
from goblinshift.files.states import ArtifactRole, Availability, HolderKind, ObjectState
from goblinshift.files.storage import FileStorage
from goblinshift.files.tables import stored_object, upload
from goblinshift.files.uploads import get_upload, request_deletion
from goblinshift.platform.db import unit_of_work
from tests.capacity.support import account_reserved, global_reserved, live_amount
from tests.files.support import create_user, ready_upload

pytestmark = pytest.mark.integration


def object_of(factory: sessionmaker[Session], upload_id: uuid.UUID) -> tuple[uuid.UUID, str]:
    with factory() as session:
        row = session.execute(
            select(stored_object.c.id, stored_object.c.storage_key)
            .join(upload, upload.c.object_id == stored_object.c.id)
            .where(upload.c.id == upload_id)
        ).one()
    return row.id, row.storage_key


def state_of(factory: sessionmaker[Session], object_id: uuid.UUID) -> str:
    with factory() as session:
        state: str = session.execute(
            select(stored_object.c.state).where(stored_object.c.id == object_id)
        ).scalar_one()
    return state


def mark(factory: sessionmaker[Session], object_ids: list[uuid.UUID]) -> int:
    with unit_of_work(factory) as session:
        return mark_missing(session, object_ids)


# --- Transição committed → missing ------------------------------------------------------------


def test_objeto_confirmado_vira_missing_e_libera_a_reserva(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = create_user(factory, "a")
    kept = ready_upload(factory, storage, owner, b"zz")
    lost = ready_upload(factory, storage, owner, b"abcd")
    lost_object, _ = object_of(factory, lost.id)
    assert account_reserved(factory, owner) == 6

    assert mark(factory, [lost_object]) == 1

    assert state_of(factory, lost_object) == ObjectState.MISSING
    assert live_amount(factory, CapacityHolder.OBJECT, lost_object) is None
    assert (account_reserved(factory, owner), global_reserved(factory)) == (2, 2)
    assert live_amount(factory, CapacityHolder.OBJECT, object_of(factory, kept.id)[0]) == 2


def test_marcar_de_novo_nao_muda_nada(factory: sessionmaker[Session], storage: FileStorage) -> None:
    owner = create_user(factory, "a")
    view = ready_upload(factory, storage, owner, b"abcd")
    object_id, _ = object_of(factory, view.id)
    mark(factory, [object_id])

    assert mark(factory, [object_id]) == 0

    assert state_of(factory, object_id) == ObjectState.MISSING
    assert account_reserved(factory, owner) == 0


def test_objeto_ja_pedido_para_exclusao_ou_removido_nao_vira_missing(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = create_user(factory, "a")
    requested = ready_upload(factory, storage, owner, b"ab")
    purged = ready_upload(factory, storage, owner, b"cd")
    requested_object, _ = object_of(factory, requested.id)
    purged_object, _ = object_of(factory, purged.id)
    with unit_of_work(factory) as session:
        # Um download aberto segura o primeiro no disco: o expurgo só leva o segundo.
        hold(session, [requested_object], HolderKind.DOWNLOAD, uuid.uuid4())
        request_deletion(session, owner, requested.id)
        request_deletion(session, owner, purged.id)
    purge_deleted_objects(factory(), storage)
    assert state_of(factory, purged_object) == ObjectState.DELETED

    assert mark(factory, [requested_object, purged_object]) == 0

    assert state_of(factory, requested_object) == ObjectState.DELETE_REQUESTED
    assert state_of(factory, purged_object) == ObjectState.DELETED


def test_upload_e_artefato_sobre_objeto_ausente_aparecem_missing(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = create_user(factory, "a")
    view = ready_upload(factory, storage, owner, b"abcd")
    object_id, _ = object_of(factory, view.id)
    with unit_of_work(factory) as session:
        reused = publish_artifact(session, owner, object_id, ArtifactRole.OUTPUT, "a.csv")

    mark(factory, [object_id])

    with factory() as session:
        assert get_upload(session, owner, view.id).availability is Availability.MISSING
        assert get_artifact(session, owner, reused.id).availability is Availability.MISSING


# --- Varredura da manutenção ------------------------------------------------------------------


def test_varredura_marca_so_o_objeto_sem_arquivo(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = create_user(factory, "a")
    kept = ready_upload(factory, storage, owner, b"zz")
    lost = ready_upload(factory, storage, owner, b"abcd")
    lost_object, lost_key = object_of(factory, lost.id)
    storage.delete(lost_key)

    report = sweep_missing_objects(factory(), storage, after=None, limit=10)

    assert (report.checked, report.marked, report.resume_after) == (2, 1, None)
    assert report.storage_unreachable is False
    assert state_of(factory, lost_object) == ObjectState.MISSING
    assert state_of(factory, object_of(factory, kept.id)[0]) == ObjectState.COMMITTED
    assert account_reserved(factory, owner) == 2


def test_varredura_anda_em_lotes_pela_ordem_do_id(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = create_user(factory, "a")
    views = [ready_upload(factory, storage, owner, bytes([i])) for i in range(3)]
    objects = sorted(object_of(factory, view.id) for view in views)
    for _, key in objects:
        storage.delete(key)

    first = sweep_missing_objects(factory(), storage, after=None, limit=2)
    second = sweep_missing_objects(factory(), storage, after=first.resume_after, limit=2)

    assert (first.checked, first.marked, first.resume_after) == (2, 2, objects[1][0])
    assert (second.checked, second.marked, second.resume_after) == (1, 1, None)
    assert {state_of(factory, object_id) for object_id, _ in objects} == {ObjectState.MISSING}


def test_varredura_ignora_objetos_que_nao_estao_confirmados(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = create_user(factory, "a")
    ready_upload(factory, storage, owner, b"zz")
    with unit_of_work(factory) as session:
        pending = session.execute(
            insert(stored_object)
            .values(owner_id=owner, storage_key="uploads/pendente-sem-arquivo")
            .returning(stored_object.c.id)
        ).scalar_one()

    report = sweep_missing_objects(factory(), storage, after=None, limit=10)

    assert (report.checked, report.marked) == (1, 0)
    assert state_of(factory, pending) == ObjectState.PENDING


@pytest.mark.parametrize("unmounted", ["sem-raiz", "raiz-vazia"])
def test_storage_sem_conteudo_nao_marca_nada(
    factory: sessionmaker[Session], storage: FileStorage, unmounted: str
) -> None:
    owner = create_user(factory, "a")
    view = ready_upload(factory, storage, owner, b"abcd")
    object_id, _ = object_of(factory, view.id)
    # Filesystem do storage desmontado: a raiz some, ou sobra o ponto de montagem vazio.
    elsewhere = FileStorage(storage.root.parent / "nao-montado")
    if unmounted == "raiz-vazia":
        elsewhere.root.mkdir()

    report = sweep_missing_objects(factory(), elsewhere, after=None, limit=10)

    assert (report.checked, report.marked, report.storage_unreachable) == (0, 0, True)
    assert state_of(factory, object_id) == ObjectState.COMMITTED
    assert account_reserved(factory, owner) == 4
