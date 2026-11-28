"""Aceitação de Run com a conta bloqueada ou com exclusão pedida (#442; plano, 3.4).

A autenticação lê a conta numa transação e a aceitação grava noutra. Sem reconferir a conta dentro
da transação da aceitação, o bloqueio ou o pedido de exclusão confirmado no meio do caminho
deixaria uma Run nova enfileirada para quem já não tem acesso.
"""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.files.storage import FileStorage
from goblinshift.files.tables import object_use
from goblinshift.identity import UnknownAccountError, block_account, request_account_deletion
from goblinshift.platform.db import unit_of_work
from goblinshift.platform.settings import LOCAL_EXECUTOR_IMAGE
from goblinshift.runs.acceptance import Accepted, accept_run
from goblinshift.runs.tables import run
from tests.lock_support import in_thread, wait_until_someone_waits_for_a_lock
from tests.runs.support import new_key, new_owner, ready_inputs, run_command

pytestmark = pytest.mark.integration

NOW = datetime(2026, 11, 28, 12, 0, tzinfo=UTC)


def accept(
    factory: sessionmaker[Session], owner_id: uuid.UUID, upload_ids: list[uuid.UUID], key: str
) -> Accepted:
    with factory() as session:
        return accept_run(
            session,
            owner_id,
            run_command(upload_ids),
            key,
            None,
            executor_image=LOCAL_EXECUTOR_IMAGE,
        )


def count_runs(factory: sessionmaker[Session], owner_id: uuid.UUID) -> int:
    with factory() as session:
        return session.execute(
            select(func.count()).select_from(run).where(run.c.owner_id == owner_id)
        ).scalar_one()


def count_holds(factory: sessionmaker[Session]) -> int:
    with factory() as session:
        return session.execute(select(func.count()).select_from(object_use)).scalar_one()


def test_aceitacao_que_esperava_e_recusada_depois_do_bloqueio(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = new_owner(factory)
    uploads = ready_inputs(factory, storage, owner)
    holds_before = count_holds(factory)

    with factory() as blocker, blocker.begin():
        assert block_account(blocker, owner, NOW)
        pending = in_thread(lambda: accept(factory, owner, uploads, new_key()))
        wait_until_someone_waits_for_a_lock(factory, "a aceitação")

    with pytest.raises(UnknownAccountError):
        pending.result()
    assert count_runs(factory, owner) == 0
    assert count_holds(factory) == holds_before


def test_aceitacao_de_conta_com_exclusao_pedida_e_recusada(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = new_owner(factory)
    uploads = ready_inputs(factory, storage, owner)
    with unit_of_work(factory) as session:
        request_account_deletion(session, owner, NOW)

    with pytest.raises(UnknownAccountError):
        accept(factory, owner, uploads, new_key())

    assert count_runs(factory, owner) == 0


def test_replay_de_conta_bloqueada_nao_cria_run(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = new_owner(factory)
    uploads = ready_inputs(factory, storage, owner)
    key = new_key()
    first = accept(factory, owner, uploads, key)
    with unit_of_work(factory) as session:
        assert block_account(session, owner, NOW)

    again = accept(factory, owner, uploads, key)

    assert again.replayed is True
    assert again.run_id == first.run_id
    assert count_runs(factory, owner) == 1
