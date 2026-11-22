"""Teto de uploads criados em 24 h por conta (#442; decisão do dono, 22/11).

O teto de vivos (D1) não limita linhas: criar, enviar vazio e excluir solta a vaga na hora, e a
linha excluída fica 90 dias para o histórico. Este teto conta todo upload criado nas últimas 24 h,
pelo relógio do banco, inclusive excluído e falho. O lote que passaria dele é recusado inteiro,
com `DailyUploadLimitError`. Os testes baixam o teto para 5 e soltam o de vivos e o pool de bytes,
para nenhuma recusa vir deles.
"""

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import func, update
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.files import uploads as uploads_module
from goblinshift.files.errors import DailyUploadLimitError, UploadSizeMismatchError
from goblinshift.files.limits import UPLOAD_CREATION_WINDOW
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
from tests.files.support import create_user

pytestmark = pytest.mark.integration

CAP = 5
LOOSE_LIVE_CAP = 1000
LOOSE_POOL_BYTES = 1024**3


@pytest.fixture
def owner(factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch) -> uuid.UUID:
    monkeypatch.setattr(uploads_module, "MAX_UPLOADS_CREATED_PER_DAY", CAP)
    monkeypatch.setattr(uploads_module, "MAX_LIVE_UPLOADS_PER_ACCOUNT", LOOSE_LIVE_CAP)
    owner_id = create_user(factory, f"conta-{uuid.uuid4()}")
    create_account_pool(factory, owner_id, LOOSE_POOL_BYTES)
    return owner_id


def create_batch(factory: sessionmaker[Session], owner_id: uuid.UUID, size: int) -> list[uuid.UUID]:
    with unit_of_work(factory) as session:
        views = create_intents(
            session, owner_id, [UploadIntent(f"f{i}.txt", 0) for i in range(size)]
        )
    return [view.id for view in views]


def test_upload_excluido_ou_falho_conta_no_teto_de_criacoes(
    factory: sessionmaker[Session], storage: FileStorage, owner: uuid.UUID
) -> None:
    *deleted, failing = create_batch(factory, owner, CAP)
    for upload_id in deleted:
        with unit_of_work(factory) as session:
            request_deletion(session, owner, upload_id)
    with factory() as session, pytest.raises(UploadSizeMismatchError):
        receive_content(session, storage, owner, failing, [b"sobra"])

    with pytest.raises(DailyUploadLimitError):
        create_batch(factory, owner, 1)


def test_upload_criado_fora_da_janela_nao_conta(
    factory: sessionmaker[Session], owner: uuid.UUID
) -> None:
    create_batch(factory, owner, CAP)
    with unit_of_work(factory) as session:
        session.execute(
            update(upload)
            .where(upload.c.owner_id == owner)
            .values(created_at=func.now() - UPLOAD_CREATION_WINDOW)
        )

    assert len(create_batch(factory, owner, CAP)) == CAP


def test_lote_que_passa_do_teto_espera_sairem_da_janela_os_que_faltam(
    factory: sessionmaker[Session], owner: uuid.UUID
) -> None:
    created = create_batch(factory, owner, CAP)
    for hours, upload_id in enumerate(created, start=1):
        with unit_of_work(factory) as session:
            session.execute(
                update(upload)
                .where(upload.c.id == upload_id)
                .values(created_at=func.now() - UPLOAD_CREATION_WINDOW + timedelta(hours=hours))
            )

    with pytest.raises(DailyUploadLimitError) as refused:
        create_batch(factory, owner, 2)

    assert timedelta(hours=2) - timedelta(minutes=1) < refused.value.retry_after
    assert refused.value.retry_after <= timedelta(hours=2)


def test_replay_do_lote_no_teto_de_criacoes_passa(
    factory: sessionmaker[Session], owner: uuid.UUID
) -> None:
    items = [(f"f{i}.txt", 0) for i in range(CAP)]
    with unit_of_work(factory) as session:
        created, _ = create_intents_once(session, owner, items, "k" * 16)

    with unit_of_work(factory) as session:
        replayed, was_replay = create_intents_once(session, owner, items, "k" * 16)

    assert was_replay
    assert [view.id for view in replayed] == [view.id for view in created]


def test_lote_acima_dos_dois_tetos_espera_pelo_que_demora_mais(
    factory: sessionmaker[Session], owner: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Os vivos vencem em até 1 h, mas a janela de criações só libera daqui a 3 h: com o Retry-After
    # dos vivos, o cliente voltaria cedo e levaria outro 429.
    monkeypatch.setattr(uploads_module, "MAX_LIVE_UPLOADS_PER_ACCOUNT", CAP)
    create_batch(factory, owner, CAP)
    with unit_of_work(factory) as session:
        session.execute(
            update(upload)
            .where(upload.c.owner_id == owner)
            .values(created_at=func.now() - UPLOAD_CREATION_WINDOW + timedelta(hours=3))
        )

    with pytest.raises(DailyUploadLimitError) as refused:
        create_batch(factory, owner, 1)

    assert refused.value.retry_after > timedelta(hours=2)
