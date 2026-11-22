"""Lote de upload criado uma vez por Idempotency-Key (#442, item 57a).

A borda HTTP (409, 422, `Idempotency-Replayed`, concorrência) fica em
`tests/api/test_uploads_idempotency.py`; aqui, a ordem do lote, o corpo canônico e o upload que a
retenção já removeu.
"""

import uuid

import pytest
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.files.errors import InvalidUploadRequestError, UploadRequestKeyReusedError
from goblinshift.files.limits import MAX_BATCH_FILES
from goblinshift.files.retention import expire_incomplete_uploads
from goblinshift.files.storage import FileStorage
from goblinshift.files.upload_requests import create_intents_once
from goblinshift.files.uploads import UploadView, request_deletion
from goblinshift.platform.db import unit_of_work
from tests.files.support import create_user

pytestmark = pytest.mark.integration

KEY = "k" * 16


def create_once(
    factory: sessionmaker[Session], owner_id: uuid.UUID, items: list[tuple[str, int]]
) -> tuple[list[UploadView], bool]:
    with unit_of_work(factory) as session:
        return create_intents_once(session, owner_id, items, KEY)


def test_replay_devolve_o_lote_na_ordem_do_pedido(factory: sessionmaker[Session]) -> None:
    owner = create_user(factory, "dono")
    items = [("z.csv", 1), ("ação.txt", 2), ("a.csv", 3)]
    created, created_replayed = create_once(factory, owner, items)

    replayed, was_replayed = create_once(factory, owner, items)

    assert (created_replayed, was_replayed) == (False, True)
    assert [view.original_name for view in replayed] == ["z.csv", "ação.txt", "a.csv"]
    assert [view.id for view in replayed] == [view.id for view in created]


def test_mesmos_itens_em_outra_ordem_sao_outro_corpo(factory: sessionmaker[Session]) -> None:
    owner = create_user(factory, "dono")
    create_once(factory, owner, [("a.csv", 1), ("b.csv", 2)])

    with pytest.raises(UploadRequestKeyReusedError):
        create_once(factory, owner, [("b.csv", 2), ("a.csv", 1)])


def test_replay_omite_o_upload_excluido_que_a_retencao_ja_removeu(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = create_user(factory, "dono")
    items = [("a.csv", 1), ("b.csv", 2)]
    (kept, removed), _ = create_once(factory, owner, items)
    with unit_of_work(factory) as session:
        request_deletion(session, owner, removed.id)
    with factory() as session:
        expire_incomplete_uploads(session, storage)

    replayed, was_replayed = create_once(factory, owner, items)

    assert was_replayed is True
    assert [view.id for view in replayed] == [kept.id]


def test_lote_invalido_com_chave_vigente_e_erro_de_validacao(
    factory: sessionmaker[Session],
) -> None:
    owner = create_user(factory, "dono")
    create_once(factory, owner, [("a.csv", 1)])

    with pytest.raises(InvalidUploadRequestError):
        create_once(factory, owner, [(f"f{i}.csv", 1) for i in range(MAX_BATCH_FILES + 1)])


def test_nome_com_surrogate_solto_e_erro_de_validacao(factory: sessionmaker[Session]) -> None:
    owner = create_user(factory, "dono")

    with pytest.raises(InvalidUploadRequestError):
        create_once(factory, owner, [("a\ud800.csv", 1)])
