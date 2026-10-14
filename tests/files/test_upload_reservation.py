"""Reserva de armazenamento dos uploads (docs/GoblinShiftPlan.md, 1.7; plano da fatia 6, Frente D).

Intenção reserva o tamanho declarado (detentor `upload`); o `ready` converte em reserva do objeto
pelo tamanho real; falha, expiração e purge físico liberam.
"""

import logging
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.capacity import (
    AccountCapacityExceededError,
    GlobalCapacityExceededError,
    HolderKind,
    ReconcileReport,
    release,
)
from goblinshift.capacity.tables import capacity_pool
from goblinshift.files.artifacts import commit_candidate, create_candidate_object, publish_artifact
from goblinshift.files.errors import UploadSizeMismatchError
from goblinshift.files.retention import (
    expire_incomplete_uploads,
    purge_deleted_objects,
    reconcile_capacity,
)
from goblinshift.files.states import ArtifactRole, ObjectState
from goblinshift.files.storage import FileStorage
from goblinshift.files.tables import stored_object, upload
from goblinshift.files.uploads import (
    UploadIntent,
    create_intents,
    receive_content,
    request_deletion,
)
from goblinshift.platform.db import unit_of_work
from tests.capacity.support import (
    account_reserved,
    create_account_pool,
    global_reserved,
    live_amount,
    set_global_max,
)
from tests.files.support import create_intent, create_user, ready_upload

pytestmark = pytest.mark.integration


def intents(factory: sessionmaker[Session], owner: uuid.UUID, *sizes: int) -> list[uuid.UUID]:
    with unit_of_work(factory) as session:
        views = create_intents(
            session, owner, [UploadIntent(f"f{i}.txt", size) for i, size in enumerate(sizes)]
        )
    return [view.id for view in views]


def object_of(factory: sessionmaker[Session], upload_id: uuid.UUID) -> uuid.UUID:
    with factory() as session:
        object_id: uuid.UUID = session.execute(
            select(upload.c.object_id).where(upload.c.id == upload_id)
        ).scalar_one()
    return object_id


def count_uploads(factory: sessionmaker[Session]) -> int:
    with factory() as session:
        return session.execute(select(func.count()).select_from(upload)).scalar_one()


# --- intenção --------------------------------------------------------------------------------


def test_intencao_reserva_o_tamanho_declarado_de_cada_upload(
    factory: sessionmaker[Session],
) -> None:
    owner = create_user(factory, "alice")

    first, second = intents(factory, owner, 10, 20)

    assert live_amount(factory, HolderKind.UPLOAD, first) == 10
    assert live_amount(factory, HolderKind.UPLOAD, second) == 20
    assert account_reserved(factory, owner) == 30
    assert global_reserved(factory) == 30


def test_lote_acima_da_cota_da_conta_e_recusado_sem_criar_nada(
    factory: sessionmaker[Session],
) -> None:
    owner = create_user(factory, "alice")
    create_account_pool(factory, owner, 25)

    with pytest.raises(AccountCapacityExceededError):
        intents(factory, owner, 10, 16)

    assert count_uploads(factory) == 0
    assert account_reserved(factory, owner) == 0


def test_lote_que_completa_a_cota_da_conta_e_aceito(factory: sessionmaker[Session]) -> None:
    owner = create_user(factory, "alice")
    create_account_pool(factory, owner, 25)

    intents(factory, owner, 10, 15)

    assert account_reserved(factory, owner) == 25


def test_lote_acima_do_teto_do_ambiente_e_recusado(factory: sessionmaker[Session]) -> None:
    owner = create_user(factory, "alice")
    set_global_max(factory, 5)

    with pytest.raises(GlobalCapacityExceededError):
        intents(factory, owner, 6)

    assert count_uploads(factory) == 0
    assert global_reserved(factory) == 0


# --- ready e falha ---------------------------------------------------------------------------


def test_upload_pronto_passa_a_reserva_para_o_objeto(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = create_user(factory, "alice")

    view = ready_upload(factory, storage, owner, b"abcd")

    assert live_amount(factory, HolderKind.UPLOAD, view.id) is None
    assert live_amount(factory, HolderKind.OBJECT, object_of(factory, view.id)) == 4
    assert account_reserved(factory, owner) == 4


def test_upload_com_tamanho_errado_libera_a_reserva(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = create_user(factory, "alice")
    intent = create_intent(factory, owner, size=5)

    with pytest.raises(UploadSizeMismatchError), factory() as session:
        receive_content(session, storage, owner, intent.id, [b"abc"])

    assert live_amount(factory, HolderKind.UPLOAD, intent.id) is None
    assert account_reserved(factory, owner) == 0


def test_upload_da_api_anterior_sem_reserva_fica_pronto_e_alerta(
    factory: sessionmaker[Session], storage: FileStorage, caplog: pytest.LogCaptureFixture
) -> None:
    owner = create_user(factory, "alice")
    intent = create_intent(factory, owner, size=3)
    # A API anterior ao deploy da 0006 não reservava: simula com a reserva já liberada.
    with unit_of_work(factory) as session:
        release(session, holder=HolderKind.UPLOAD, holder_id=intent.id)

    with caplog.at_level(logging.WARNING), factory() as session:
        view = receive_content(session, storage, owner, intent.id, [b"abc"])

    assert view.state == "ready"
    assert live_amount(factory, HolderKind.OBJECT, object_of(factory, intent.id)) is None
    assert any(
        getattr(r, "fields", {}).get("alert") == "upload_without_reservation"
        for r in caplog.records
    )


# --- expiração e purge físico ----------------------------------------------------------------


def test_upload_incompleto_expirado_libera_a_reserva(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = create_user(factory, "alice")
    intent = create_intent(factory, owner, size=7)
    with unit_of_work(factory) as session:
        session.execute(
            update(upload)
            .where(upload.c.id == intent.id)
            .values(expires_at=func.now() - timedelta(seconds=1))
        )

    assert expire_incomplete_uploads(factory(), storage) == 1

    assert live_amount(factory, HolderKind.UPLOAD, intent.id) is None
    assert account_reserved(factory, owner) == 0


def test_purge_fisico_libera_a_reserva_do_objeto(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = create_user(factory, "alice")
    kept = ready_upload(factory, storage, owner, b"zz")
    view = ready_upload(factory, storage, owner, b"abcd")
    with unit_of_work(factory) as session:
        request_deletion(session, owner, view.id)
    assert account_reserved(factory, owner) == 6

    report = purge_deleted_objects(factory(), storage)

    assert report.objects == 1
    assert live_amount(factory, HolderKind.OBJECT, object_of(factory, view.id)) is None
    assert account_reserved(factory, owner) == 2
    assert global_reserved(factory) == 2
    assert live_amount(factory, HolderKind.OBJECT, object_of(factory, kept.id)) == 2


# --- reconciliação ---------------------------------------------------------------------------


def drop_object_reservation(factory: sessionmaker[Session], object_id: uuid.UUID) -> None:
    """Simula objeto criado pela API anterior ao deploy: nenhuma reserva viva, pool em zero."""
    with unit_of_work(factory) as session:
        release(session, holder=HolderKind.OBJECT, holder_id=object_id)


def test_reconcilia_adotando_upload_pronto_sem_reserva(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = create_user(factory, "alice")
    view = ready_upload(factory, storage, owner, b"abcde")
    object_id = object_of(factory, view.id)
    drop_object_reservation(factory, object_id)

    report = reconcile_capacity(factory(), limit=100)

    assert report == ReconcileReport(pools_corrected=0, objects_adopted=1)
    assert live_amount(factory, HolderKind.OBJECT, object_id) == 5
    assert account_reserved(factory, owner) == 5


def test_reconciliacao_repetida_nao_adota_de_novo(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = create_user(factory, "alice")
    view = ready_upload(factory, storage, owner, b"abcde")
    drop_object_reservation(factory, object_of(factory, view.id))
    reconcile_capacity(factory(), limit=100)

    report = reconcile_capacity(factory(), limit=100)

    assert report == ReconcileReport(pools_corrected=0, objects_adopted=0)
    assert account_reserved(factory, owner) == 5


def test_reconciliacao_nao_adota_candidato_coberto_pela_run(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = create_user(factory, "alice")
    candidate = candidate_object(factory, owner, size=9)

    report = reconcile_capacity(factory(), limit=100)

    assert report.objects_adopted == 0
    assert live_amount(factory, HolderKind.OBJECT, candidate) is None


def test_reconciliacao_adota_artefato_publicado_sem_reserva(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = create_user(factory, "alice")
    candidate = candidate_object(factory, owner, size=9)
    with unit_of_work(factory) as session:
        publish_artifact(session, owner, candidate, ArtifactRole.OUTPUT, "saida.csv")

    report = reconcile_capacity(factory(), limit=100)

    assert report.objects_adopted == 1
    assert live_amount(factory, HolderKind.OBJECT, candidate) == 9


def test_reconciliacao_corrige_pool_divergente(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = create_user(factory, "alice")
    ready_upload(factory, storage, owner, b"abc")
    with unit_of_work(factory) as session:
        session.execute(
            update(capacity_pool).where(capacity_pool.c.owner_id == owner).values(reserved=0)
        )

    report = reconcile_capacity(factory(), limit=100)

    assert report.pools_corrected == 1
    assert account_reserved(factory, owner) == 3


def candidate_object(factory: sessionmaker[Session], owner: uuid.UUID, size: int) -> uuid.UUID:
    """Objeto `committed` de uma tentativa, ainda candidato (sem artefato)."""
    attempt_id = uuid.uuid4()
    with unit_of_work(factory) as session:
        candidate = create_candidate_object(session, owner, uuid.uuid4(), attempt_id, 1)
        commit_candidate(session, owner, candidate.object_id, b"\x01" * 32, size)
        state = session.execute(
            select(stored_object.c.state).where(stored_object.c.id == candidate.object_id)
        ).scalar_one()
    assert state == ObjectState.COMMITTED
    return candidate.object_id
