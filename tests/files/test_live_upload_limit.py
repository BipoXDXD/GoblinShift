"""Teto de uploads vivos por conta (#442, item 53a; decisão D1 = B).

Vivo é o upload `created`, `receiving` ou `ready` que não foi excluído nem venceu pelo relógio do
banco. O lote que passaria do teto é recusado inteiro, com `LiveUploadLimitError`. Os testes
baixam o teto para 5; o pool de bytes da conta fica folgado, para nenhuma recusa vir da cota.
"""

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.files import uploads as uploads_module
from goblinshift.files.errors import LiveUploadLimitError, UploadSizeMismatchError
from goblinshift.files.states import UploadState
from goblinshift.files.storage import FileStorage
from goblinshift.files.tables import upload
from goblinshift.files.upload_requests import create_intents_once
from goblinshift.files.uploads import (
    UploadIntent,
    create_intents,
    receive_content,
    request_deletion,
)
from goblinshift.platform.db import unit_of_work
from tests.capacity.support import create_account_pool
from tests.files.support import create_user, ready_upload

pytestmark = pytest.mark.integration

CAP = 5
LOOSE_POOL_BYTES = 1024**3


@pytest.fixture
def owner(factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch) -> uuid.UUID:
    monkeypatch.setattr(uploads_module, "MAX_LIVE_UPLOADS_PER_ACCOUNT", CAP)
    owner_id = create_user(factory, f"conta-{uuid.uuid4()}")
    create_account_pool(factory, owner_id, LOOSE_POOL_BYTES)
    return owner_id


def create_batch(factory: sessionmaker[Session], owner_id: uuid.UUID, size: int) -> list[uuid.UUID]:
    with unit_of_work(factory) as session:
        views = create_intents(
            session, owner_id, [UploadIntent(f"f{i}.txt", 0) for i in range(size)]
        )
    return [view.id for view in views]


def set_upload(factory: sessionmaker[Session], upload_id: uuid.UUID, **values: object) -> None:
    with unit_of_work(factory) as session:
        session.execute(update(upload).where(upload.c.id == upload_id).values(**values))


def count_uploads(factory: sessionmaker[Session], owner_id: uuid.UUID) -> int:
    with factory() as session:
        return session.execute(
            select(func.count()).select_from(upload).where(upload.c.owner_id == owner_id)
        ).scalar_one()


@pytest.mark.parametrize(
    ("live", "batch", "accepted"),
    [(4, 1, True), (4, 2, False), (5, 1, False), (0, 5, True), (0, 6, False)],
)
def test_teto_de_uploads_vivos_por_conta(
    factory: sessionmaker[Session], owner: uuid.UUID, live: int, batch: int, accepted: bool
) -> None:
    if live:
        create_batch(factory, owner, live)

    if accepted:
        create_batch(factory, owner, batch)
        assert count_uploads(factory, owner) == live + batch
    else:
        with pytest.raises(LiveUploadLimitError):
            create_batch(factory, owner, batch)
        assert count_uploads(factory, owner) == live


def test_lotes_paralelos_so_os_que_cabem_passam(
    factory: sessionmaker[Session], owner: uuid.UUID
) -> None:
    contenders = CAP + 1
    barrier = threading.Barrier(contenders)

    def create_one() -> str:
        barrier.wait()
        try:
            create_batch(factory, owner, 1)
        except LiveUploadLimitError:
            return "recusado"
        return "ok"

    with ThreadPoolExecutor(contenders) as pool:
        results = list(pool.map(lambda _: create_one(), range(contenders)))

    assert sorted(results) == ["ok"] * CAP + ["recusado"]
    assert count_uploads(factory, owner) == CAP


def test_upload_vencido_excluido_ou_falho_nao_conta(
    factory: sessionmaker[Session], storage: FileStorage, owner: uuid.UUID
) -> None:
    expired_a, expired_b, deleted_a, deleted_b, failing = create_batch(factory, owner, CAP)
    for upload_id in (expired_a, expired_b):
        set_upload(factory, upload_id, expires_at=func.now() - timedelta(seconds=1))
    for upload_id in (deleted_a, deleted_b):
        with unit_of_work(factory) as session:
            request_deletion(session, owner, upload_id)
    with factory() as session, pytest.raises(UploadSizeMismatchError):
        receive_content(session, storage, owner, failing, [b"sobra"])

    create_batch(factory, owner, CAP)

    assert count_uploads(factory, owner) == 2 * CAP


def test_upload_pronto_ou_recebendo_conta(
    factory: sessionmaker[Session], storage: FileStorage, owner: uuid.UUID
) -> None:
    ready_upload(factory, storage, owner, content=b"")
    receiving, *_ = create_batch(factory, owner, CAP - 1)
    set_upload(factory, receiving, state=UploadState.RECEIVING)

    with pytest.raises(LiveUploadLimitError):
        create_batch(factory, owner, 1)


def test_uploads_de_outra_conta_nao_contam(
    factory: sessionmaker[Session], owner: uuid.UUID
) -> None:
    other = create_user(factory, f"outra-{uuid.uuid4()}")
    create_batch(factory, other, CAP)

    create_batch(factory, owner, CAP)

    assert count_uploads(factory, owner) == CAP


def test_recusa_espera_vencerem_os_uploads_que_faltam_para_o_lote_caber(
    factory: sessionmaker[Session], owner: uuid.UUID
) -> None:
    for hours, upload_id in enumerate(create_batch(factory, owner, CAP), start=1):
        set_upload(factory, upload_id, expires_at=func.now() + timedelta(hours=hours))

    with pytest.raises(LiveUploadLimitError) as refused:
        create_batch(factory, owner, 2)

    assert timedelta(hours=2) - timedelta(minutes=1) < refused.value.retry_after
    assert refused.value.retry_after <= timedelta(hours=2)


def test_replay_do_lote_no_teto_nao_conta_de_novo(
    factory: sessionmaker[Session], owner: uuid.UUID
) -> None:
    items = [(f"f{i}.txt", 0) for i in range(CAP)]
    with unit_of_work(factory) as session:
        created, _ = create_intents_once(session, owner, items, "k" * 16)

    with unit_of_work(factory) as session:
        replayed, was_replay = create_intents_once(session, owner, items, "k" * 16)

    assert was_replay
    assert [view.id for view in replayed] == [view.id for view in created]
    assert count_uploads(factory, owner) == CAP


def test_espera_sem_os_vivos_que_sumiram_depois_da_contagem_fica_em_zero(
    factory: sessionmaker[Session], owner: uuid.UUID
) -> None:
    """Entre a contagem e a leitura da espera, os vivos podem vencer ou ser excluídos.

    A espera pede mais vivos do que restam, como se a contagem tivesse visto os que sumiram: em
    vez de um 500, fica em zero, e a borda HTTP aplica o piso do `Retry-After`.
    """
    [upload_id] = create_batch(factory, owner, 1)
    with unit_of_work(factory) as session:
        request_deletion(session, owner, upload_id)

    with unit_of_work(factory) as session:
        wait = uploads_module._until_expired(session, owner, 1)

    assert wait == timedelta(0)
