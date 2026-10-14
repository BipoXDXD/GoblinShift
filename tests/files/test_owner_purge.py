"""Purga dos arquivos de uma conta em exclusão (docs/GoblinShiftPlan.md, 1.8; Frente C).

A purga só pede a exclusão; o arquivo sai do disco pelo caminho normal da retenção
(`purge_deleted_objects` e `expire_incomplete_uploads`), que espera os usos abertos.
"""

import uuid

import pytest
from sqlalchemy import Table, func, select
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.files.artifacts import (
    commit_candidate,
    create_candidate_object,
    finish_download,
    open_for_download,
    publish_artifact,
)
from goblinshift.files.purge import purge_owner_batch
from goblinshift.files.retention import expire_incomplete_uploads, purge_deleted_objects
from goblinshift.files.states import ArtifactRole, TransferDirection
from goblinshift.files.storage import FileStorage
from goblinshift.files.tables import artifact, object_use, stored_object, transfer_slot, upload
from goblinshift.files.transfers import acquire_transfer_slot
from goblinshift.platform.db import unit_of_work
from tests.files.support import create_intent, create_user, ready_upload

pytestmark = pytest.mark.integration

LIMIT = 100


def new_artifact(
    factory: sessionmaker[Session], storage: FileStorage, owner_id: uuid.UUID
) -> uuid.UUID:
    """Artefato publicado pelo caminho do worker: candidato, gravação, confirmação, publicação."""
    content = b"saida"
    with unit_of_work(factory) as session:
        candidate = create_candidate_object(session, owner_id, uuid.uuid4(), uuid.uuid4(), 1)
    sha256, size = storage.create_exclusive(
        candidate.storage_key, [content], max_bytes=len(content)
    )
    with unit_of_work(factory) as session:
        commit_candidate(session, owner_id, candidate.object_id, sha256, size)
        return publish_artifact(
            session, owner_id, candidate.object_id, ArtifactRole.OUTPUT, "saida.csv"
        ).id


def committed_candidate(
    factory: sessionmaker[Session], storage: FileStorage, owner_id: uuid.UUID
) -> uuid.UUID:
    """Saída confirmada que não chegou a virar artefato (tentativa vencida)."""
    content = b"descartada"
    with unit_of_work(factory) as session:
        candidate = create_candidate_object(session, owner_id, uuid.uuid4(), uuid.uuid4(), 1)
    sha256, size = storage.create_exclusive(
        candidate.storage_key, [content], max_bytes=len(content)
    )
    with unit_of_work(factory) as session:
        commit_candidate(session, owner_id, candidate.object_id, sha256, size)
    return candidate.object_id


def purge_batch(factory: sessionmaker[Session], owner_id: uuid.UUID, limit: int = LIMIT) -> bool:
    with unit_of_work(factory) as session:
        return purge_owner_batch(session, owner_id, limit)


def retention_cycle(factory: sessionmaker[Session], storage: FileStorage) -> None:
    """O que a manutenção faz a cada ciclo com os arquivos pedidos para exclusão."""
    expire_incomplete_uploads(factory(), storage)
    purge_deleted_objects(factory(), storage)


def purge_until_done(
    factory: sessionmaker[Session], storage: FileStorage, owner_id: uuid.UUID
) -> int:
    """Ciclos de purga + retenção até a purga terminar; devolve quantos ciclos levou."""
    for cycle in range(1, 20):
        if purge_batch(factory, owner_id):
            return cycle
        retention_cycle(factory, storage)
    raise AssertionError("a purga não terminou")


def rows_of(factory: sessionmaker[Session], owner_id: uuid.UUID) -> dict[str, int]:
    tables: dict[str, Table] = {
        "stored_object": stored_object,
        "upload": upload,
        "artifact": artifact,
        "transfer_slot": transfer_slot,
    }
    with factory() as session:
        return {
            name: session.execute(
                select(func.count()).select_from(table).where(table.c.owner_id == owner_id)
            ).scalar_one()
            for name, table in tables.items()
        }


def object_use_rows(factory: sessionmaker[Session]) -> int:
    with factory() as session:
        count: int = session.execute(select(func.count()).select_from(object_use)).scalar_one()
    return count


def storage_keys(factory: sessionmaker[Session], owner_id: uuid.UUID) -> list[str]:
    with factory() as session:
        return list(
            session.execute(
                select(stored_object.c.storage_key).where(stored_object.c.owner_id == owner_id)
            ).scalars()
        )


EMPTY = {"stored_object": 0, "upload": 0, "artifact": 0, "transfer_slot": 0}


def test_conta_sem_arquivos_termina_na_hora(factory: sessionmaker[Session]) -> None:
    assert purge_batch(factory, create_user(factory, "ana")) is True


def test_remove_uploads_artefatos_candidatos_e_vagas_do_disco_e_do_banco(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = create_user(factory, "ana")
    ready_upload(factory, storage, owner)
    create_intent(factory, owner)
    new_artifact(factory, storage, owner)
    committed_candidate(factory, storage, owner)
    with unit_of_work(factory) as session:
        acquire_transfer_slot(session, owner, TransferDirection.UPLOAD)
    keys = storage_keys(factory, owner)

    purge_until_done(factory, storage, owner)

    assert rows_of(factory, owner) == EMPTY
    assert object_use_rows(factory) == 0
    assert all(not storage.root.joinpath(*key.split("/")).exists() for key in keys)


def test_primeiro_lote_so_pede_a_exclusao_e_nao_termina(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = create_user(factory, "ana")
    upload_view = ready_upload(factory, storage, owner)

    done = purge_batch(factory, owner)

    assert done is False
    with factory() as session:
        row = session.execute(
            select(upload.c.deleted_at, stored_object.c.state)
            .join(stored_object, stored_object.c.id == upload.c.object_id)
            .where(upload.c.id == upload_view.id)
        ).one()
    assert row.deleted_at is not None
    assert row.state == "delete_requested"


def test_lote_pequeno_avanca_ate_terminar(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = create_user(factory, "ana")
    for _ in range(3):
        ready_upload(factory, storage, owner)

    batches = 0
    while not purge_batch(factory, owner, limit=1):
        retention_cycle(factory, storage)
        batches += 1
        assert batches < 20

    assert rows_of(factory, owner) == EMPTY


def test_purga_interrompida_e_retomada_termina(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = create_user(factory, "ana")
    ready_upload(factory, storage, owner)
    new_artifact(factory, storage, owner)

    # Um lote confirmado e o processo cai antes da retenção; ao voltar, repete do começo.
    purge_batch(factory, owner)
    purge_batch(factory, owner)

    purge_until_done(factory, storage, owner)
    assert rows_of(factory, owner) == EMPTY


def test_download_aberto_segura_a_purga_ate_terminar(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = create_user(factory, "ana")
    artifact_id = new_artifact(factory, storage, owner)
    with unit_of_work(factory) as session:
        ticket = open_for_download(session, owner, artifact_id)
    [key] = storage_keys(factory, owner)

    for _ in range(3):
        assert purge_batch(factory, owner) is False
        retention_cycle(factory, storage)
    assert storage.root.joinpath(*key.split("/")).exists()

    with unit_of_work(factory) as session:
        finish_download(session, ticket)
    purge_until_done(factory, storage, owner)

    assert rows_of(factory, owner) == EMPTY
    assert not storage.root.joinpath(*key.split("/")).exists()


def test_outra_conta_fica_intacta(factory: sessionmaker[Session], storage: FileStorage) -> None:
    owner, other = create_user(factory, "ana"), create_user(factory, "bia")
    ready_upload(factory, storage, owner)
    other_upload = ready_upload(factory, storage, other)
    other_artifact = new_artifact(factory, storage, other)
    before = rows_of(factory, other)
    other_keys = storage_keys(factory, other)

    purge_until_done(factory, storage, owner)

    assert rows_of(factory, other) == before
    with factory() as session:
        upload_row = session.execute(select(upload).where(upload.c.id == other_upload.id)).one()
        artifact_row = session.execute(
            select(artifact).where(artifact.c.id == other_artifact)
        ).one()
        states = set(
            session.execute(
                select(stored_object.c.state).where(stored_object.c.owner_id == other)
            ).scalars()
        )
    assert upload_row.deleted_at is None
    assert artifact_row.deleted_at is None
    assert states == {"committed"}
    assert all(storage.root.joinpath(*key.split("/")).exists() for key in other_keys)
