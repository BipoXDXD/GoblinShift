"""O histórico de execuções acha o upload pelo índice `(owner_id, upload_id)` de `run_input`.

`run_input.owner_id` é sempre o dono do upload referenciado: a FK composta
`(owner_id, upload_id) -> upload(owner_id, id)` garante isso no banco. Por isso o predicado de
`runs.retention` correlaciona também o dono, e a limpeza das linhas de `files` usa o índice em vez
de varrer `run_input` por upload.
"""

import re
import uuid
from datetime import datetime

import pytest
from sqlalchemy import insert, select, text, update
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.files.retention import FILE_ROWS_AFTER, delete_old_file_rows
from goblinshift.files.states import ObjectState
from goblinshift.files.storage import FileStorage
from goblinshift.files.tables import stored_object, upload
from goblinshift.files.uploads import request_deletion
from goblinshift.platform.db import unit_of_work
from goblinshift.runs.retention import HISTORY_REFERENCES
from goblinshift.runs.store import db_now
from goblinshift.runs.tables import run_input
from tests.files.support import ready_upload
from tests.runs.support import insert_finished_run, new_owner

pytestmark = pytest.mark.integration

LIMIT = 10
OWNER_UPLOAD_INDEX = "run_input_owner_id_upload_id_idx"
OWNER_CONDITION = re.compile(r"owner_id = upload\.owner_id|upload\.owner_id = run_input\.owner_id")


def old_deleted_upload(
    factory: sessionmaker[Session], storage: FileStorage, now: datetime, *, in_history: bool
) -> uuid.UUID:
    """Upload excluído, com objeto fora do disco e 90 dias de idade; `in_history` o põe numa Run."""
    owner = new_owner(factory)
    upload_id = ready_upload(factory, storage, owner).id
    created_at = now - FILE_ROWS_AFTER
    with unit_of_work(factory) as session:
        request_deletion(session, owner, upload_id)
        object_id = session.execute(
            select(upload.c.object_id).where(upload.c.id == upload_id)
        ).scalar_one()
        session.execute(
            update(upload).where(upload.c.id == upload_id).values(created_at=created_at)
        )
        session.execute(
            update(stored_object)
            .where(stored_object.c.id == object_id)
            .values(state=ObjectState.DELETED, created_at=created_at)
        )
    if in_history:
        run_id = insert_finished_run(factory, owner, created_at)
        with unit_of_work(factory) as session:
            session.execute(
                insert(run_input).values(
                    run_id=run_id, position=0, owner_id=owner, upload_id=upload_id, outcome="ok"
                )
            )
    return upload_id


def test_upload_no_historico_nao_e_apagado_e_o_de_fora_sai(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    with factory() as session:
        now = db_now(session)
    in_history = old_deleted_upload(factory, storage, now, in_history=True)
    outside = old_deleted_upload(factory, storage, now, in_history=False)

    with unit_of_work(factory) as session:
        removed = delete_old_file_rows(session, now, LIMIT, HISTORY_REFERENCES)

    with factory() as session:
        remaining = set(session.execute(select(upload.c.id)).scalars())
    assert removed == 1
    assert remaining == {in_history}
    assert outside not in remaining


def test_predicado_do_historico_so_marca_o_upload_referenciado(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    with factory() as session:
        now = db_now(session)
    in_history = old_deleted_upload(factory, storage, now, in_history=True)
    old_deleted_upload(factory, storage, now, in_history=False)

    with factory() as session:
        marked = set(
            session.execute(
                select(upload.c.id).where(HISTORY_REFERENCES.upload(upload.c.id))
            ).scalars()
        )

    assert marked == {in_history}


def test_predicado_do_historico_usa_o_indice_de_dono_e_upload(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    with factory() as session:
        now = db_now(session)
    old_deleted_upload(factory, storage, now, in_history=True)
    statement = select(upload.c.id).where(HISTORY_REFERENCES.upload(upload.c.id))

    with factory() as session:
        sql = str(statement.compile(session.get_bind()))
        # Com tabelas minúsculas o planejador escolheria a varredura sequencial por custo;
        # desligá-la mostra como o predicado consegue usar o índice.
        session.execute(text("SET LOCAL enable_seqscan = off"))
        plan = "\n".join(session.execute(text(f"EXPLAIN {sql}")).scalars())

    # O PostgreSQL 18 também varre o índice inteiro quando só o upload é correlacionado, então o
    # nome do índice não basta: o dono precisa estar na condição de junção/pesquisa.
    assert OWNER_UPLOAD_INDEX in plan, plan
    assert OWNER_CONDITION.search(plan), plan
