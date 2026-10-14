"""Usos ativos, entradas prontas para `runs`, artefatos e downloads (3.2, 3.4 e 3.6)."""

import hashlib
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.files.artifacts import (
    commit_candidate,
    create_candidate_object,
    finish_download,
    open_for_download,
    publish_artifact,
)
from goblinshift.files.errors import (
    ArtifactGoneError,
    ArtifactNotFoundError,
    InputNotAvailableError,
    ObjectNotAvailableError,
)
from goblinshift.files.objects import hold, release
from goblinshift.files.states import ArtifactRole, HolderKind
from goblinshift.files.storage import FileStorage
from goblinshift.files.tables import artifact, object_use, upload
from goblinshift.files.uploads import lock_ready_uploads, request_deletion
from goblinshift.platform.db import unit_of_work
from tests.files.support import create_intent, create_user, ready_upload

pytestmark = pytest.mark.integration


def open_uses(factory: sessionmaker[Session]) -> int:
    with factory() as session:
        count: int = session.execute(
            select(func.count()).select_from(object_use).where(object_use.c.ended_at.is_(None))
        ).scalar_one()
    return count


def published_artifact(
    factory: sessionmaker[Session], storage: FileStorage, owner: uuid.UUID, content: bytes
) -> uuid.UUID:
    run_id, attempt_id = uuid.uuid4(), uuid.uuid4()
    with unit_of_work(factory) as session:
        candidate = create_candidate_object(session, owner, run_id, attempt_id, generation=1)
    sha256, size = storage.create_exclusive(candidate.storage_key, [content], max_bytes=100)
    with unit_of_work(factory) as session:
        commit_candidate(session, owner, candidate.object_id, sha256, size)
        view = publish_artifact(session, owner, candidate.object_id, ArtifactRole.OUTPUT)
    return view.id


# --- Entradas prontas para uma execução -------------------------------------------------------


def test_entradas_prontas_voltam_na_ordem_pedida(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = create_user(factory, "a")
    first = ready_upload(factory, storage, owner, b"1")
    second = ready_upload(factory, storage, owner, b"22")

    with unit_of_work(factory) as session:
        inputs = lock_ready_uploads(session, owner, [second.id, first.id])

    assert [(i.upload_id, i.size_bytes) for i in inputs] == [(second.id, 2), (first.id, 1)]
    assert inputs[0].sha256 == hashlib.sha256(b"22").digest()
    assert inputs[0].original_name == second.original_name


def test_qualquer_entrada_indisponivel_gera_um_erro_unico(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = create_user(factory, "a")
    other = create_user(factory, "b")
    ok = ready_upload(factory, storage, owner)
    alien = ready_upload(factory, storage, other)
    incomplete = create_intent(factory, owner)
    deleted = ready_upload(factory, storage, owner)
    with unit_of_work(factory) as session:
        request_deletion(session, owner, deleted.id)
    expired = ready_upload(factory, storage, owner)
    with unit_of_work(factory) as session:
        session.execute(
            update(upload)
            .where(upload.c.id == expired.id)
            .values(expires_at=func.now() - timedelta(seconds=1))
        )

    for unavailable in [alien.id, incomplete.id, deleted.id, expired.id, uuid.uuid4()]:
        with pytest.raises(InputNotAvailableError) as error, unit_of_work(factory) as session:
            lock_ready_uploads(session, owner, [ok.id, unavailable])
        assert str(error.value) == "entrada indisponível"


# --- hold / release ---------------------------------------------------------------------------


def test_hold_repetido_registra_um_uso_so(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = create_user(factory, "a")
    view = ready_upload(factory, storage, owner)
    run_id = uuid.uuid4()
    with unit_of_work(factory) as session:
        [ready] = lock_ready_uploads(session, owner, [view.id])

    for _ in range(2):
        with unit_of_work(factory) as session:
            hold(session, [ready.object_id], HolderKind.RUN, run_id)

    assert open_uses(factory) == 1


def test_release_repetido_e_sucesso(factory: sessionmaker[Session], storage: FileStorage) -> None:
    owner = create_user(factory, "a")
    view = ready_upload(factory, storage, owner)
    run_id = uuid.uuid4()
    with unit_of_work(factory) as session:
        [ready] = lock_ready_uploads(session, owner, [view.id])
        hold(session, [ready.object_id], HolderKind.RUN, run_id)

    for _ in range(2):
        with unit_of_work(factory) as session:
            release(session, HolderKind.RUN, run_id)

    assert open_uses(factory) == 0


def test_hold_em_objeto_com_exclusao_pedida_e_recusado(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = create_user(factory, "a")
    view = ready_upload(factory, storage, owner)
    with unit_of_work(factory) as session:
        [ready] = lock_ready_uploads(session, owner, [view.id])
    with unit_of_work(factory) as session:
        request_deletion(session, owner, view.id)

    with pytest.raises(ObjectNotAvailableError), unit_of_work(factory) as session:
        hold(session, [ready.object_id], HolderKind.RUN, uuid.uuid4())

    assert open_uses(factory) == 0


# --- Artefatos e downloads --------------------------------------------------------------------


def test_candidato_usa_chave_da_execucao_e_da_geracao(factory: sessionmaker[Session]) -> None:
    owner = create_user(factory, "a")
    run_id = uuid.uuid4()

    with unit_of_work(factory) as session:
        candidate = create_candidate_object(session, owner, run_id, uuid.uuid4(), generation=3)

    assert candidate.storage_key.startswith(f"runs/{run_id}/g3/")


def test_artefato_publicado_expira_em_48_horas(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = create_user(factory, "a")
    run_id = uuid.uuid4()
    with unit_of_work(factory) as session:
        candidate = create_candidate_object(session, owner, run_id, uuid.uuid4(), generation=1)
    sha256, size = storage.create_exclusive(candidate.storage_key, [b"saida"], max_bytes=100)

    with unit_of_work(factory) as session:
        commit_candidate(session, owner, candidate.object_id, sha256, size)
        view = publish_artifact(session, owner, candidate.object_id, ArtifactRole.REPORT)

    assert view.role is ArtifactRole.REPORT
    assert (view.size_bytes, view.sha256) == (5, hashlib.sha256(b"saida").digest())
    assert view.expires_at - view.created_at == timedelta(hours=48)


def test_publicar_objeto_ainda_pendente_e_recusado(factory: sessionmaker[Session]) -> None:
    owner = create_user(factory, "a")
    with unit_of_work(factory) as session:
        candidate = create_candidate_object(session, owner, uuid.uuid4(), uuid.uuid4(), 1)

    with pytest.raises(ObjectNotAvailableError), unit_of_work(factory) as session:
        publish_artifact(session, owner, candidate.object_id, ArtifactRole.OUTPUT)


def test_download_registra_uso_e_libera_ao_terminar(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = create_user(factory, "a")
    artifact_id = published_artifact(factory, storage, owner, b"resultado")

    with unit_of_work(factory) as session:
        ticket = open_for_download(session, owner, artifact_id)
    assert open_uses(factory) == 1
    with storage.open(ticket.storage_key) as stream:
        assert stream.read() == b"resultado"
    assert ticket.size_bytes == 9

    with unit_of_work(factory) as session:
        finish_download(session, ticket)
    assert open_uses(factory) == 0


def test_download_de_artefato_alheio_ou_inexistente_da_o_mesmo_erro(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = create_user(factory, "a")
    other = create_user(factory, "b")
    artifact_id = published_artifact(factory, storage, owner, b"segredo")

    messages = []
    for target in [artifact_id, uuid.uuid4()]:
        with pytest.raises(ArtifactNotFoundError) as error, unit_of_work(factory) as session:
            open_for_download(session, other, target)
        messages.append(str(error.value))

    assert messages[0] == messages[1]
    assert open_uses(factory) == 0


def test_download_de_artefato_expirado_e_recusado(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = create_user(factory, "a")
    artifact_id = published_artifact(factory, storage, owner, b"x")
    with unit_of_work(factory) as session:
        session.execute(
            update(artifact)
            .where(artifact.c.id == artifact_id)
            .values(expires_at=func.now() - timedelta(seconds=1))
        )

    with pytest.raises(ArtifactGoneError), unit_of_work(factory) as session:
        open_for_download(session, owner, artifact_id)
    assert open_uses(factory) == 0


def test_upload_excluido_reaproveitado_como_artefato_some_das_entradas_mas_nao_do_download(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    # Detox: a saída aponta para o mesmo objeto da entrada; excluir o upload não apaga a saída.
    owner = create_user(factory, "a")
    view = ready_upload(factory, storage, owner, b"compartilhado")
    with unit_of_work(factory) as session:
        [ready] = lock_ready_uploads(session, owner, [view.id])
        shared = publish_artifact(session, owner, ready.object_id, ArtifactRole.OUTPUT)

    with unit_of_work(factory) as session:
        request_deletion(session, owner, view.id)

    with pytest.raises(InputNotAvailableError), unit_of_work(factory) as session:
        lock_ready_uploads(session, owner, [view.id])
    with unit_of_work(factory) as session:
        ticket = open_for_download(session, owner, shared.id)
    with storage.open(ticket.storage_key) as stream:
        assert stream.read() == b"compartilhado"
