"""Publicação do pacote comum e reaproveitamento do objeto da entrada (ADRs 019 e 020).

PostgreSQL e executor reais: o Downloads Detox copia os bytes de cada entrada para `out/`, e o
worker, ao guardar os candidatos, aponta o artefato para o objeto da entrada quando o SHA-256 e o
tamanho que ele mesmo calculou batem e o objeto ainda está `committed`.
"""

import uuid
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.files.candidates import attempts_with_loose_candidates
from goblinshift.files.storage import FileStorage
from goblinshift.files.tables import artifact, stored_object, upload
from goblinshift.files.uploads import request_deletion
from goblinshift.platform.db import unit_of_work
from goblinshift.runs.tables import run_input, run_output
from goblinshift.runs.worker.claim import ClaimedAttempt
from goblinshift.runs.worker.collection import Collected
from goblinshift.runs.worker.coordinator import Coordinator, StoredCandidate
from tests.downloads_detox.samples import png
from tests.runs.worker_support import (
    attempts,
    claim,
    coordinator,
    coordinator_config,
    create_user,
    local_runner,
    make_eligible_now,
    queue_run,
    run_row,
)

pytestmark = pytest.mark.integration

DETOX = "downloads-detox"
PHOTO = png(7)
NOTE = b"lista de compras\n"


def published(
    factory: sessionmaker[Session], run_id: uuid.UUID
) -> dict[str, tuple[str, uuid.UUID]]:
    """Nome lógico → (papel, objeto) de cada artefato publicado da Run."""
    with factory() as session:
        rows = session.execute(
            select(run_output.c.logical_name, artifact.c.role, artifact.c.object_id)
            .join(artifact, artifact.c.id == run_output.c.artifact_id)
            .where(run_output.c.run_id == run_id)
            .order_by(run_output.c.position)
        ).all()
    return {row.logical_name: (row.role, row.object_id) for row in rows}


def input_objects(factory: sessionmaker[Session], run_id: uuid.UUID) -> list[uuid.UUID]:
    with factory() as session:
        return list(
            session.execute(
                select(upload.c.object_id)
                .join(run_input, run_input.c.upload_id == upload.c.id)
                .where(run_input.c.run_id == run_id)
                .order_by(run_input.c.position)
            ).scalars()
        )


def input_uploads(factory: sessionmaker[Session], run_id: uuid.UUID) -> list[uuid.UUID]:
    with factory() as session:
        return list(
            session.execute(
                select(run_input.c.upload_id)
                .where(run_input.c.run_id == run_id)
                .order_by(run_input.c.position)
            ).scalars()
        )


def candidates_of(factory: sessionmaker[Session], attempt_id: uuid.UUID) -> int:
    with factory() as session:
        count: int = session.execute(
            select(func.count())
            .select_from(stored_object)
            .where(stored_object.c.attempt_id == attempt_id)
        ).scalar_one()
    return count


def loose_attempts(factory: sessionmaker[Session]) -> list[uuid.UUID]:
    with factory() as session:
        return attempts_with_loose_candidates(session, limit=100)


def delete_upload(
    factory: sessionmaker[Session], owner_id: uuid.UUID, upload_id: uuid.UUID
) -> None:
    with unit_of_work(factory) as session:
        request_deletion(session, owner_id, upload_id)


def test_detox_publica_conteudo_relatorio_manifesto_e_pacote_nos_quatro_papeis(
    factory: sessionmaker[Session], storage: FileStorage, work_root: Path
) -> None:
    run_id = queue_run(
        factory,
        storage,
        create_user(factory),
        (("Foto.png", PHOTO), ("nota.txt", NOTE)),
        recipe_id=DETOX,
    )

    coordinator(factory, storage, local_runner(work_root)).run(claim(factory, storage))

    assert run_row(factory, run_id)["status"] == "succeeded"
    assert {name: role for name, (role, _) in published(factory, run_id).items()} == {
        "images/Foto.png": "output",
        "text/nota.txt": "output",
        "report.json": "report",
        "manifest.json": "manifest",
        "downloads-detox.zip": "package",
    }


def test_saida_identica_a_entrada_aponta_para_o_objeto_da_entrada_sem_candidato(
    factory: sessionmaker[Session], storage: FileStorage, work_root: Path
) -> None:
    run_id = queue_run(
        factory,
        storage,
        create_user(factory),
        (("Foto.png", PHOTO), ("nota.txt", NOTE)),
        recipe_id=DETOX,
    )
    claimed = claim(factory, storage)

    coordinator(factory, storage, local_runner(work_root)).run(claimed)

    artifacts = published(factory, run_id)
    photo_object, note_object = input_objects(factory, run_id)
    assert artifacts["images/Foto.png"][1] == photo_object
    assert artifacts["text/nota.txt"][1] == note_object
    # Só relatório, manifesto e pacote viraram candidatos da tentativa.
    assert candidates_of(factory, claimed.attempt_id) == 3


def test_entradas_do_mesmo_tamanho_reaproveitam_cada_uma_o_proprio_objeto(
    factory: sessionmaker[Session], storage: FileStorage, work_root: Path
) -> None:
    first, second = png(1), png(2)
    assert len(first) == len(second)
    assert first != second
    run_id = queue_run(
        factory,
        storage,
        create_user(factory),
        (("um.png", first), ("dois.png", second)),
        recipe_id=DETOX,
    )

    coordinator(factory, storage, local_runner(work_root)).run(claim(factory, storage))

    first_object, second_object = input_objects(factory, run_id)
    artifacts = published(factory, run_id)
    assert artifacts["images/um.png"][1] == first_object
    assert artifacts["images/dois.png"][1] == second_object


def test_duplicata_aponta_a_unica_saida_para_o_objeto_da_primeira_ocorrencia(
    factory: sessionmaker[Session], storage: FileStorage, work_root: Path
) -> None:
    run_id = queue_run(
        factory, storage, create_user(factory), (("a.txt", NOTE), ("b.txt", NOTE)), recipe_id=DETOX
    )

    coordinator(factory, storage, local_runner(work_root)).run(claim(factory, storage))

    first_object, _ = input_objects(factory, run_id)
    outputs = {
        name: object_id
        for name, (role, object_id) in published(factory, run_id).items()
        if role == "output"
    }
    assert outputs == {"text/a.txt": first_object}


def test_entrada_excluida_antes_da_publicacao_grava_copia(
    factory: sessionmaker[Session], storage: FileStorage, work_root: Path
) -> None:
    owner = create_user(factory)
    run_id = queue_run(factory, storage, owner, (("Foto.png", PHOTO),), recipe_id=DETOX)
    claimed = claim(factory, storage)
    [upload_id] = input_uploads(factory, run_id)
    delete_upload(factory, owner, upload_id)

    coordinator(factory, storage, local_runner(work_root)).run(claimed)

    assert run_row(factory, run_id)["status"] == "succeeded"
    [photo_object] = input_objects(factory, run_id)
    copy_object = published(factory, run_id)["images/Foto.png"][1]
    assert copy_object != photo_object
    with factory() as session:
        key = session.execute(
            select(stored_object.c.storage_key).where(stored_object.c.id == copy_object)
        ).scalar_one()
    assert key.startswith(f"runs/{run_id}/g1/")
    with storage.open(key) as content:
        assert content.read() == PHOTO


class _DeletesInputAfterStoring(Coordinator):
    """Exclusão do upload entre a gravação dos candidatos e a publicação (corrida real)."""

    owner_id: uuid.UUID
    upload_id: uuid.UUID

    def store_candidates(
        self, claimed: ClaimedAttempt, collected: Collected
    ) -> list[StoredCandidate]:
        stored = super().store_candidates(claimed, collected)
        delete_upload(self._factory, self.owner_id, self.upload_id)
        return stored


def test_entrada_excluida_entre_gravar_e_publicar_vira_retentativa_que_grava_copia(
    factory: sessionmaker[Session], storage: FileStorage, work_root: Path
) -> None:
    owner = create_user(factory)
    run_id = queue_run(factory, storage, owner, (("Foto.png", PHOTO),), recipe_id=DETOX)
    [upload_id] = input_uploads(factory, run_id)
    racing = _DeletesInputAfterStoring(
        factory, storage, local_runner(work_root), coordinator_config()
    )
    racing.owner_id, racing.upload_id = owner, upload_id

    first_attempt = claim(factory, storage)
    racing.run(first_attempt)

    waiting = run_row(factory, run_id)
    assert (waiting["status"], waiting["phase"]) == ("running", "retry_wait")
    assert attempts(factory, run_id)[0]["error_code"] == "EXECUTOR_UNAVAILABLE"
    assert published(factory, run_id) == {}
    # A retentativa não descarta na hora: a manutenção coleta os candidatos soltos (fatia 4).
    assert first_attempt.attempt_id in loose_attempts(factory)
    make_eligible_now(factory, run_id)
    coordinator(factory, storage, local_runner(work_root)).run(claim(factory, storage))
    assert run_row(factory, run_id)["status"] == "succeeded"
    [photo_object] = input_objects(factory, run_id)
    assert published(factory, run_id)["images/Foto.png"][1] != photo_object
