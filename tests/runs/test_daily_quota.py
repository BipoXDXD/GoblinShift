"""Cotas diárias por conta: 30 execuções e 2 GiB de entrada em 24 h corridas (1.7; 3.3).

`daily_usage` recebe o instante, então as fronteiras da janela são testadas sem relógio real. A
aceitação usa o relógio do banco; ali os casos ficam a minutos da fronteira.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import insert, select, update
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.files.storage import FileStorage
from goblinshift.files.tables import stored_object, upload
from goblinshift.platform.db import unit_of_work
from goblinshift.platform.settings import LOCAL_EXECUTOR_IMAGE
from goblinshift.runs.acceptance import (
    DailyQuotaExceededError,
    InvalidRunCommandError,
    RunCommand,
    accept_run,
)
from goblinshift.runs.lifecycle import Status
from goblinshift.runs.quotas import (
    MAX_INPUT_BYTES_PER_WINDOW,
    MAX_RUNS_PER_WINDOW,
    DailyUsage,
    active_run_id,
    daily_usage,
)
from goblinshift.runs.store import db_now
from goblinshift.runs.tables import run, run_input
from tests.runs.support import (
    count_rows,
    insert_finished_run,
    new_key,
    new_owner,
    ready_inputs,
    run_command,
)

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)
GIB = 1024**3


def link_input(
    factory: sessionmaker[Session], run_id: uuid.UUID, owner: uuid.UUID, upload_id: uuid.UUID
) -> None:
    with unit_of_work(factory) as session:
        position = len(
            session.execute(select(run_input.c.position).where(run_input.c.run_id == run_id)).all()
        )
        session.execute(
            insert(run_input).values(
                run_id=run_id, position=position, owner_id=owner, upload_id=upload_id
            )
        )


def pretend_size(factory: sessionmaker[Session], upload_id: uuid.UUID, size: int) -> None:
    """Muda só o tamanho registrado do objeto: 2 GiB de verdade não cabem num teste."""
    with unit_of_work(factory) as session:
        object_id = session.execute(
            select(upload.c.object_id).where(upload.c.id == upload_id)
        ).scalar_one()
        session.execute(
            update(stored_object).where(stored_object.c.id == object_id).values(size_bytes=size)
        )


def usage_at(factory: sessionmaker[Session], owner: uuid.UUID, now: datetime) -> DailyUsage:
    with factory() as session:
        return daily_usage(session, owner, now)


# --- daily_usage -----------------------------------------------------------------------------


def test_conta_sem_execucoes_tem_uso_zero(factory: sessionmaker[Session]) -> None:
    assert usage_at(factory, new_owner(factory), NOW) == DailyUsage(0, 0, None)


def test_conta_execucoes_e_bytes_de_entrada_da_janela(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = new_owner(factory)
    first, second = ready_inputs(factory, storage, owner, 2)
    older = insert_finished_run(factory, owner, NOW - timedelta(hours=5))
    newer = insert_finished_run(factory, owner, NOW - timedelta(hours=1))
    link_input(factory, older, owner, first)
    link_input(factory, newer, owner, first)
    link_input(factory, newer, owner, second)
    pretend_size(factory, first, 100)
    pretend_size(factory, second, 7)

    usage = usage_at(factory, owner, NOW)

    # A mesma entrada processada por duas execuções conta duas vezes.
    assert usage == DailyUsage(runs=2, input_bytes=207, oldest_run_at=NOW - timedelta(hours=5))


def test_execucao_criada_exatamente_24h_antes_ja_saiu_da_janela(
    factory: sessionmaker[Session],
) -> None:
    owner = new_owner(factory)
    insert_finished_run(factory, owner, NOW - timedelta(hours=24))

    assert usage_at(factory, owner, NOW).runs == 0


def test_execucao_criada_um_segundo_antes_das_24h_ainda_conta(
    factory: sessionmaker[Session],
) -> None:
    owner = new_owner(factory)
    created = NOW - timedelta(hours=24) + timedelta(seconds=1)
    insert_finished_run(factory, owner, created)

    assert usage_at(factory, owner, NOW) == DailyUsage(1, 0, created)


def test_execucao_de_25h_atras_nao_conta(factory: sessionmaker[Session]) -> None:
    owner = new_owner(factory)
    insert_finished_run(factory, owner, NOW - timedelta(hours=25))

    assert usage_at(factory, owner, NOW).runs == 0


def test_execucoes_de_outra_conta_nao_contam(factory: sessionmaker[Session]) -> None:
    owner = new_owner(factory)
    insert_finished_run(factory, new_owner(factory), NOW - timedelta(hours=1))

    assert usage_at(factory, owner, NOW) == DailyUsage(0, 0, None)


def test_execucao_falha_tambem_conta(factory: sessionmaker[Session]) -> None:
    owner = new_owner(factory)
    insert_finished_run(
        factory, owner, NOW - timedelta(hours=1), status=Status.FAILED, error_code="QUEUE_EXPIRED"
    )

    assert usage_at(factory, owner, NOW).runs == 1


def test_active_run_id_mostra_so_a_execucao_nao_terminal(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = new_owner(factory)
    insert_finished_run(factory, owner, NOW - timedelta(hours=1))
    with factory() as session:
        assert active_run_id(session, owner) is None

    accepted = accept(factory, owner, run_command(ready_inputs(factory, storage, owner)))

    with factory() as session:
        assert active_run_id(session, owner) == accepted


# --- aceitação -------------------------------------------------------------------------------


def accept(factory: sessionmaker[Session], owner: uuid.UUID, command: RunCommand) -> uuid.UUID:
    with factory() as session:
        accepted = accept_run(
            session, owner, command, new_key(), None, executor_image=LOCAL_EXECUTOR_IMAGE
        )
    return accepted.run_id


def database_now(factory: sessionmaker[Session]) -> datetime:
    with factory() as session:
        return db_now(session)


def fill_runs(factory: sessionmaker[Session], owner: uuid.UUID, count: int, age: timedelta) -> None:
    created = database_now(factory) - age
    for _ in range(count):
        insert_finished_run(factory, owner, created)


def test_31a_execucao_em_24h_e_recusada_com_o_prazo_da_mais_antiga(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = new_owner(factory)
    fill_runs(factory, owner, MAX_RUNS_PER_WINDOW, timedelta(hours=20))
    command = run_command(ready_inputs(factory, storage, owner))
    before = count_rows(factory)

    with pytest.raises(DailyQuotaExceededError) as refused:
        accept(factory, owner, command)

    # A mais antiga sai da janela em cerca de 4 h.
    assert timedelta(hours=3, minutes=59) <= refused.value.retry_after <= timedelta(hours=4)
    assert count_rows(factory) == before


def test_30a_execucao_em_24h_e_aceita(factory: sessionmaker[Session], storage: FileStorage) -> None:
    owner = new_owner(factory)
    fill_runs(factory, owner, MAX_RUNS_PER_WINDOW - 1, timedelta(hours=20))

    accept(factory, owner, run_command(ready_inputs(factory, storage, owner)))


def test_execucoes_de_mais_de_24h_nao_impedem_a_aceitacao(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = new_owner(factory)
    fill_runs(factory, owner, MAX_RUNS_PER_WINDOW, timedelta(hours=24, minutes=5))

    accept(factory, owner, run_command(ready_inputs(factory, storage, owner)))


def entry_bytes_used(
    factory: sessionmaker[Session], storage: FileStorage, owner: uuid.UUID, used: int
) -> None:
    """Uma execução de 1 h atrás que processou `used` bytes de entrada."""
    [previous] = ready_inputs(factory, storage, owner)
    finished = insert_finished_run(factory, owner, database_now(factory) - timedelta(hours=1))
    link_input(factory, finished, owner, previous)
    pretend_size(factory, previous, used)


def test_entrada_que_passa_de_2_gib_em_24h_e_recusada(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = new_owner(factory)
    [new_input] = ready_inputs(factory, storage, owner)
    entry_bytes_used(factory, storage, owner, MAX_INPUT_BYTES_PER_WINDOW - 7)
    pretend_size(factory, new_input, 8)
    before = count_rows(factory)

    with pytest.raises(DailyQuotaExceededError) as refused:
        accept(factory, owner, run_command([new_input]))

    assert timedelta(hours=22, minutes=59) <= refused.value.retry_after <= timedelta(hours=23)
    assert count_rows(factory) == before


def test_entrada_que_completa_2_gib_em_24h_e_aceita(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = new_owner(factory)
    [new_input] = ready_inputs(factory, storage, owner)
    entry_bytes_used(factory, storage, owner, MAX_INPUT_BYTES_PER_WINDOW - 7)
    pretend_size(factory, new_input, 7)

    accept(factory, owner, run_command([new_input]))


def test_execucao_cuja_entrada_sozinha_passa_de_2_gib_e_invalida(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = new_owner(factory)
    first, second = ready_inputs(factory, storage, owner, 2)
    pretend_size(factory, first, GIB)
    pretend_size(factory, second, GIB + 1)

    with pytest.raises(InvalidRunCommandError, match="input_upload_ids"):
        accept(factory, owner, run_command([first, second]))


def test_detox_recusa_entradas_acima_do_teto_da_versao(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = new_owner(factory)
    first, second = ready_inputs(factory, storage, owner, 2)
    pretend_size(factory, first, 240 * 1024**2)
    pretend_size(factory, second, 240 * 1024**2 + 1)
    command = RunCommand("downloads-detox", 1, (first, second), {})

    with pytest.raises(InvalidRunCommandError, match="input_upload_ids"):
        accept(factory, owner, command)


def test_detox_aceita_entradas_no_teto_da_versao(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = new_owner(factory)
    first, second = ready_inputs(factory, storage, owner, 2)
    pretend_size(factory, first, 240 * 1024**2)
    pretend_size(factory, second, 240 * 1024**2)

    accept(factory, owner, RunCommand("downloads-detox", 1, (first, second), {}))


def test_quota_nao_conta_execucao_recusada(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = new_owner(factory)
    fill_runs(factory, owner, MAX_RUNS_PER_WINDOW, timedelta(hours=1))
    command = run_command(ready_inputs(factory, storage, owner))
    with pytest.raises(DailyQuotaExceededError):
        accept(factory, owner, command)

    with factory() as session:
        runs = session.execute(select(run.c.id).where(run.c.owner_id == owner)).all()
    assert len(runs) == MAX_RUNS_PER_WINDOW
