"""Reserva do teto de saída da Run e conversão na publicação (1.7; ADR 024, D-5).

A aceitação reserva o teto de saída com o detentor `run`. A publicação passa o tamanho de cada
objeto publicado para uma reserva do objeto, e o estado terminal libera o resto.
"""

import logging
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.capacity import (
    AccountCapacityExceededError,
    GlobalCapacityExceededError,
    HolderKind,
)
from goblinshift.capacity.tables import capacity_pool
from goblinshift.files.storage import FileStorage
from goblinshift.files.tables import artifact, stored_object, upload
from goblinshift.platform.db import unit_of_work
from goblinshift.platform.settings import LOCAL_EXECUTOR_IMAGE
from goblinshift.runs.acceptance import RUN_OUTPUT_CEILING_BYTES, accept_run
from goblinshift.runs.cancellation import cancel_run
from goblinshift.runs.tables import run_output
from tests.capacity.support import (
    account_reserved,
    global_reserved,
    live_amount,
    set_global_max,
)
from tests.runs.support import count_rows, new_key, ready_inputs, run_command
from tests.runs.worker_support import claim, coordinator, create_user, local_runner, queue_run

pytestmark = pytest.mark.integration

MIB = 1024**2


def accept(factory: sessionmaker[Session], storage: FileStorage, owner: uuid.UUID) -> uuid.UUID:
    command = run_command(ready_inputs(factory, storage, owner))
    with factory() as session:
        accepted = accept_run(
            session, owner, command, new_key(), None, executor_image=LOCAL_EXECUTOR_IMAGE
        )
    return accepted.run_id


def input_bytes(factory: sessionmaker[Session], owner: uuid.UUID) -> int:
    with factory() as session:
        sizes = session.execute(
            select(stored_object.c.size_bytes)
            .join(upload, upload.c.object_id == stored_object.c.id)
            .where(upload.c.owner_id == owner)
        ).scalars()
        return sum(sizes)


def published_objects(factory: sessionmaker[Session], run_id: uuid.UUID) -> dict[uuid.UUID, int]:
    with factory() as session:
        rows = session.execute(
            select(stored_object.c.id, stored_object.c.size_bytes)
            .join(artifact, artifact.c.object_id == stored_object.c.id)
            .join(run_output, run_output.c.artifact_id == artifact.c.id)
            .where(run_output.c.run_id == run_id)
        ).all()
    return {row.id: row.size_bytes for row in rows}


def test_teto_de_saida_e_de_cerca_de_1_gib() -> None:
    assert RUN_OUTPUT_CEILING_BYTES == (480 + 8 + 8 + 512) * MIB


def test_aceitacao_reserva_o_teto_de_saida_para_a_run(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = create_user(factory)

    run_id = accept(factory, storage, owner)

    assert live_amount(factory, HolderKind.RUN, run_id) == RUN_OUTPUT_CEILING_BYTES
    assert account_reserved(factory, owner) == RUN_OUTPUT_CEILING_BYTES + input_bytes(
        factory, owner
    )


def test_aceitacao_sem_espaco_na_conta_e_recusada_sem_gravar_nada(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = create_user(factory)
    [upload_id] = ready_inputs(factory, storage, owner)
    room = input_bytes(factory, owner) + RUN_OUTPUT_CEILING_BYTES - 1
    _shrink_account(factory, owner, room)
    before = count_rows(factory)

    with pytest.raises(AccountCapacityExceededError), factory() as session:
        accept_run(
            session,
            owner,
            run_command([upload_id]),
            new_key(),
            None,
            executor_image=LOCAL_EXECUTOR_IMAGE,
        )

    assert count_rows(factory) == before
    assert account_reserved(factory, owner) == input_bytes(factory, owner)


def test_aceitacao_que_completa_o_teto_da_conta_e_aceita(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = create_user(factory)
    [upload_id] = ready_inputs(factory, storage, owner)
    _shrink_account(factory, owner, input_bytes(factory, owner) + RUN_OUTPUT_CEILING_BYTES)

    with factory() as session:
        accept_run(
            session,
            owner,
            run_command([upload_id]),
            new_key(),
            None,
            executor_image=LOCAL_EXECUTOR_IMAGE,
        )


def test_aceitacao_sem_espaco_no_ambiente_e_recusada(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = create_user(factory)
    [upload_id] = ready_inputs(factory, storage, owner)
    set_global_max(factory, RUN_OUTPUT_CEILING_BYTES)
    before = count_rows(factory)

    with pytest.raises(GlobalCapacityExceededError), factory() as session:
        accept_run(
            session,
            owner,
            run_command([upload_id]),
            new_key(),
            None,
            executor_image=LOCAL_EXECUTOR_IMAGE,
        )

    assert count_rows(factory) == before


def test_publicacao_passa_cada_objeto_publicado_para_reserva_propria_e_libera_o_resto(
    factory: sessionmaker[Session], storage: FileStorage, work_root: Path
) -> None:
    owner = create_user(factory)
    run_id = accept(factory, storage, owner)

    coordinator(factory, storage, local_runner(work_root)).run(claim(factory, storage))

    published = published_objects(factory, run_id)
    assert len(published) == 5  # saída, amostra (preview), relatório, manifesto e ZIP
    for object_id, size in published.items():
        assert live_amount(factory, HolderKind.OBJECT, object_id) == size
    assert live_amount(factory, HolderKind.RUN, run_id) is None
    expected = input_bytes(factory, owner) + sum(published.values())
    assert account_reserved(factory, owner) == expected
    assert global_reserved(factory) == expected


def test_cancelamento_na_fila_libera_o_teto_da_run(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = create_user(factory)
    run_id = accept(factory, storage, owner)

    cancel_run(factory, storage, owner, run_id)

    assert live_amount(factory, HolderKind.RUN, run_id) is None
    assert account_reserved(factory, owner) == input_bytes(factory, owner)


def test_publicacao_de_run_aceita_antes_do_deploy_nao_falha_e_alerta(
    factory: sessionmaker[Session],
    storage: FileStorage,
    work_root: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    owner = create_user(factory)
    # `queue_run` grava a Run direto no banco, como a API anterior: sem reserva de saída.
    run_id = queue_run(factory, storage, owner)

    with caplog.at_level(logging.WARNING):
        coordinator(factory, storage, local_runner(work_root)).run(claim(factory, storage))

    published = published_objects(factory, run_id)
    assert len(published) == 5
    # A saída limpa igual à entrada reaproveita o objeto do upload, que já tem reserva; os
    # objetos novos da tentativa (preview, relatório, manifesto e ZIP) ficam sem reserva até a
    # reconciliação.
    assert sum(live_amount(factory, HolderKind.OBJECT, o) is None for o in published) == 4
    assert any(
        getattr(r, "fields", {}).get("alert") == "run_without_reservation" for r in caplog.records
    )


def _shrink_account(factory: sessionmaker[Session], owner: uuid.UUID, max_amount: int) -> None:
    """Teto da conta reduzido depois de os uploads já terem reservado."""
    with unit_of_work(factory) as session:
        session.execute(
            update(capacity_pool)
            .where(capacity_pool.c.owner_id == owner)
            .values(max_amount=max_amount)
        )
