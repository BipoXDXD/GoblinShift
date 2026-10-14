"""Aceitação de execuções com PostgreSQL real (docs/GoblinShiftPlan.md, 1.6, 1.7 e 3.4)."""

import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import replace
from datetime import timedelta

import pytest
from sqlalchemy import Engine, func, select, text, update
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.catalog import Catalog, RecipeVersionRetiredError, VersionStatus
from goblinshift.catalog.versions import CSV_JANITOR_V1
from goblinshift.files.errors import InputNotAvailableError
from goblinshift.files.storage import FileStorage
from goblinshift.files.tables import object_use
from goblinshift.platform.db import make_engine, make_session_factory, unit_of_work
from goblinshift.platform.settings import LOCAL_EXECUTOR_IMAGE, Settings
from goblinshift.runs import acceptance
from goblinshift.runs.acceptance import (
    ADVISORY_CLASS_QUEUE,
    MAX_QUEUED_RUNS,
    NOTIFY_CHANNEL,
    QUEUE_LOCK_OBJECT,
    Accepted,
    ActiveRunLimitError,
    IdempotencyInProgressError,
    IdempotencyKeyInvalidError,
    IdempotencyKeyReusedError,
    InvalidRunCommandError,
    QueueFullError,
    RunCommand,
    accept_run,
    validate_run,
)
from goblinshift.runs.tables import idempotency_record, run, run_input
from tests.runs.support import (
    count_rows,
    finish_run,
    new_key,
    new_owner,
    ready_inputs,
    run_command,
    unavailable_upload,
)

pytestmark = pytest.mark.integration

TRACEPARENT = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"
CSV_MAX_INPUTS = 5
WAIT_DEADLINE_SECONDS = 5.0


def accept(
    factory: sessionmaker[Session],
    owner_id: uuid.UUID,
    command: RunCommand,
    key: str | None = None,
) -> Accepted:
    with factory() as session:
        return accept_run(
            session,
            owner_id,
            command,
            key or new_key(),
            TRACEPARENT,
            executor_image=LOCAL_EXECUTOR_IMAGE,
        )


def test_a_execucao_fixa_a_imagem_do_executor_recebida(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = new_owner(factory)
    image = "ghcr.io/bipoxdxd/goblinshift-executor@sha256:" + "d" * 64
    with factory() as session:
        accepted = accept_run(
            session,
            owner,
            run_command(ready_inputs(factory, storage, owner)),
            new_key(),
            None,
            executor_image=image,
        )

    with factory() as session:
        pinned = session.execute(
            select(run.c.executor_digest).where(run.c.id == accepted.run_id)
        ).scalar_one()
    assert pinned == image


def queue_run(
    factory: sessionmaker[Session], storage: FileStorage, owner_id: uuid.UUID | None = None
) -> Accepted:
    owner = owner_id or new_owner(factory)
    return accept(factory, owner, run_command(ready_inputs(factory, storage, owner)))


def test_aceita_a_execucao_na_fila_com_entradas_na_ordem(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = new_owner(factory)
    inputs = ready_inputs(factory, storage, owner, 3)
    command = run_command(inputs, {"trim_columns": ["a"]})

    accepted = accept(factory, owner, command)

    assert accepted.replayed is False
    with factory() as session:
        row = session.execute(
            select(run, (run.c.queue_expires_at - run.c.created_at).label("window")).where(
                run.c.id == accepted.run_id
            )
        ).one()
        positions = session.execute(
            select(run_input.c.position, run_input.c.upload_id)
            .where(run_input.c.run_id == accepted.run_id)
            .order_by(run_input.c.position)
        ).all()
        holds = session.execute(
            select(func.count()).where(
                object_use.c.holder_kind == "run",
                object_use.c.holder_id == accepted.run_id,
                object_use.c.ended_at.is_(None),
            )
        ).scalar_one()
        record = session.execute(select(idempotency_record)).one()
    assert row.owner_id == owner
    assert row.status == "queued"
    assert row.window == timedelta(hours=2)
    assert row.eligible_at == row.created_at
    assert row.executor_digest == "local-dev"
    assert row.traceparent == TRACEPARENT
    assert row.parameters["trim_columns"] == ["a"]
    assert row.parameters["formula_guard"] == "none"
    assert row.limits["max_inputs"] == CSV_MAX_INPUTS
    assert positions == [(0, inputs[0]), (1, inputs[1]), (2, inputs[2])]
    assert holds == len(inputs)
    assert record.run_id == accepted.run_id
    assert record.operation == "create_run"
    assert record.expires_at - record.created_at == timedelta(hours=24)
    assert accepted.idempotency_expires_at == record.expires_at


def test_aceitacao_notifica_o_canal_da_fila_no_commit(
    engine: Engine, factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = new_owner(factory)
    inputs = ready_inputs(factory, storage, owner)
    with engine.connect() as listener:
        listener.execute(text(f"LISTEN {NOTIFY_CHANNEL}"))
        listener.commit()

        accept(factory, owner, run_command(inputs))

        raw = listener.connection.driver_connection
        assert raw is not None
        notifications = list(raw.notifies(timeout=WAIT_DEADLINE_SECONDS, stop_after=1))
    assert [n.channel for n in notifications] == [NOTIFY_CHANNEL]


def test_mesma_chave_e_mesmo_comando_devolve_a_mesma_execucao_sem_criar_nada(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = new_owner(factory)
    command = run_command(ready_inputs(factory, storage, owner, 2))
    key = new_key()
    first = accept(factory, owner, command, key)
    before = count_rows(factory)

    again = accept(factory, owner, command, key)

    assert again == replace(first, replayed=True)
    assert count_rows(factory) == before


def test_replay_vale_com_outra_ordem_das_chaves_dos_parametros(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = new_owner(factory)
    inputs = ready_inputs(factory, storage, owner)
    key = new_key()
    first = accept(factory, owner, run_command(inputs, {"trim_columns": [], "filters": []}), key)

    again = accept(factory, owner, run_command(inputs, {"filters": [], "trim_columns": []}), key)

    assert again.run_id == first.run_id
    assert again.replayed is True


def test_replay_vale_com_defaults_explicitos(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = new_owner(factory)
    inputs = ready_inputs(factory, storage, owner)
    key = new_key()
    first = accept(factory, owner, run_command(inputs, {}), key)

    again = accept(factory, owner, run_command(inputs, {"formula_guard": "none"}), key)

    assert again.run_id == first.run_id


@pytest.mark.parametrize(
    "change",
    [
        pytest.param(lambda ids: run_command(ids, {"trim_columns": ["a"]}), id="parametros"),
        pytest.param(lambda ids: run_command(list(reversed(ids))), id="ordem-dos-uploads"),
        pytest.param(lambda ids: run_command(ids[:1]), id="menos-uploads"),
    ],
)
def test_mesma_chave_com_outro_comando_e_recusada(
    factory: sessionmaker[Session],
    storage: FileStorage,
    change: Callable[[list[uuid.UUID]], RunCommand],
) -> None:
    owner = new_owner(factory)
    inputs = ready_inputs(factory, storage, owner, 2)
    key = new_key()
    accept(factory, owner, run_command(inputs), key)
    before = count_rows(factory)

    with pytest.raises(IdempotencyKeyReusedError):
        accept(factory, owner, change(inputs), key)

    assert count_rows(factory) == before


def test_chave_de_outra_conta_nao_interfere(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    key = new_key()
    first = queue_run_with_key(factory, storage, key)

    second = queue_run_with_key(factory, storage, key)

    assert second.replayed is False
    assert second.run_id != first.run_id


def queue_run_with_key(factory: sessionmaker[Session], storage: FileStorage, key: str) -> Accepted:
    owner = new_owner(factory)
    return accept(factory, owner, run_command(ready_inputs(factory, storage, owner)), key)


@pytest.mark.parametrize("key", [None, "a" * 15, "a" * 65, "a" * 16 + "!"])
def test_chave_invalida_e_recusada_antes_de_qualquer_escrita(
    factory: sessionmaker[Session], storage: FileStorage, key: str | None
) -> None:
    owner = new_owner(factory)
    command = run_command(ready_inputs(factory, storage, owner))
    before = count_rows(factory)

    with factory() as session, pytest.raises(IdempotencyKeyInvalidError):
        accept_run(session, owner, command, key, None, executor_image=LOCAL_EXECUTOR_IMAGE)

    assert count_rows(factory) == before


@pytest.mark.parametrize("length", [16, 64])
def test_chave_nos_limites_de_tamanho_e_aceita(
    factory: sessionmaker[Session], storage: FileStorage, length: int
) -> None:
    owner = new_owner(factory)
    command = run_command(ready_inputs(factory, storage, owner))

    accepted = accept(factory, owner, command, "k" * length)

    assert accepted.replayed is False


def test_chave_vencida_cria_nova_execucao(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = new_owner(factory)
    command = run_command(ready_inputs(factory, storage, owner))
    key = new_key()
    first = accept(factory, owner, command, key)
    finish_run(factory, first.run_id)
    with unit_of_work(factory) as session:
        session.execute(
            update(idempotency_record).values(expires_at=func.now() - timedelta(seconds=1))
        )

    second = accept(factory, owner, command, key)

    assert second.replayed is False
    assert second.run_id != first.run_id
    with factory() as session:
        record = session.execute(select(idempotency_record)).one()
    assert record.run_id == second.run_id
    assert count_rows(factory)["run"] == 2


def test_chave_vencida_aceita_outro_comando(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = new_owner(factory)
    inputs = ready_inputs(factory, storage, owner, 2)
    key = new_key()
    first = accept(factory, owner, run_command(inputs[:1]), key)
    finish_run(factory, first.run_id)
    with unit_of_work(factory) as session:
        session.execute(
            update(idempotency_record).values(expires_at=func.now() - timedelta(seconds=1))
        )

    second = accept(factory, owner, run_command(inputs[1:]), key)

    assert second.replayed is False


def test_requisicao_com_a_chave_em_andamento_recebe_in_progress(
    engine: Engine, factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = new_owner(factory)
    command = run_command(ready_inputs(factory, storage, owner))
    key = new_key()
    outcome: list[Accepted | Exception] = []

    # A fila global travada segura a requisição original depois de ela pegar a chave.
    with engine.connect() as blocker:
        blocker.execute(select(func.pg_advisory_xact_lock(ADVISORY_CLASS_QUEUE, QUEUE_LOCK_OBJECT)))
        original = threading.Thread(
            target=record_outcome, args=(outcome, factory, owner, command, key)
        )
        original.start()
        wait_for_advisory_waiter(engine)

        with pytest.raises(IdempotencyInProgressError):
            accept(factory, owner, command, key)

        blocker.rollback()
    original.join()

    [first] = outcome
    assert isinstance(first, Accepted)
    assert first.replayed is False
    assert accept(factory, owner, command, key) == replace(first, replayed=True)
    assert count_rows(factory)["run"] == 1


def record_outcome(
    outcome: list[Accepted | Exception],
    factory: sessionmaker[Session],
    owner_id: uuid.UUID,
    command: RunCommand,
    key: str,
) -> None:
    try:
        outcome.append(accept(factory, owner_id, command, key))
    except Exception as error:  # o teste confere o tipo depois do join
        outcome.append(error)


def wait_for_advisory_waiter(engine: Engine) -> None:
    """Espera, consultando o banco, até uma sessão ficar bloqueada num advisory lock."""
    deadline = time.monotonic() + WAIT_DEADLINE_SECONDS
    with engine.connect() as conn:
        while time.monotonic() < deadline:
            waiting = conn.execute(
                text("SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND NOT granted")
            ).scalar_one()
            conn.rollback()
            if waiting:
                return
    raise AssertionError("ninguém ficou esperando o advisory lock")


def test_duas_requisicoes_simultaneas_com_a_mesma_chave_criam_uma_execucao(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = new_owner(factory)
    command = run_command(ready_inputs(factory, storage, owner))
    key = new_key()

    outcomes = run_concurrently(2, lambda: accept(factory, owner, command, key))

    created = [o for o in outcomes if isinstance(o, Accepted) and not o.replayed]
    others = [o for o in outcomes if o not in created]
    assert len(created) == 1
    for other in others:
        assert isinstance(other, IdempotencyInProgressError) or (
            isinstance(other, Accepted) and other.run_id == created[0].run_id
        ), other
    assert count_rows(factory)["run"] == 1


def run_concurrently(count: int, action: Callable[[], Accepted]) -> list[Accepted | Exception]:
    barrier = threading.Barrier(count)
    outcomes: list[Accepted | Exception] = []
    lock = threading.Lock()

    def worker() -> None:
        barrier.wait()
        try:
            result: Accepted | Exception = action()
        except Exception as error:  # o teste confere o tipo
            result = error
        with lock:
            outcomes.append(result)

    threads = [threading.Thread(target=worker) for _ in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return outcomes


def test_segunda_execucao_ativa_na_conta_e_recusada(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = new_owner(factory)
    queue_run(factory, storage, owner)
    command = run_command(ready_inputs(factory, storage, owner))
    before = count_rows(factory)

    with pytest.raises(ActiveRunLimitError):
        accept(factory, owner, command)

    assert count_rows(factory) == before


def test_conta_aceita_nova_execucao_depois_que_a_anterior_termina(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = new_owner(factory)
    finish_run(factory, queue_run(factory, storage, owner).run_id)

    accepted = queue_run(factory, storage, owner)

    assert accepted.replayed is False


def test_fila_global_com_dez_aguardando_recusa_a_seguinte(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    for _ in range(MAX_QUEUED_RUNS):
        queue_run(factory, storage)
    owner = new_owner(factory)
    command = run_command(ready_inputs(factory, storage, owner))
    before = count_rows(factory)

    with pytest.raises(QueueFullError):
        accept(factory, owner, command)

    assert count_rows(factory) == before


def test_fila_com_nove_aguardando_aceita_a_decima(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    for _ in range(MAX_QUEUED_RUNS - 1):
        queue_run(factory, storage)

    accepted = queue_run(factory, storage)

    assert accepted.replayed is False


def test_execucao_que_ja_comecou_nao_ocupa_a_fila(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    started = queue_run(factory, storage)
    with unit_of_work(factory) as session:
        session.execute(
            update(run)
            .where(run.c.id == started.run_id)
            .values(
                status="running",
                phase="inspecting",
                claim_count=1,
                generation=1,
                deadline_at=func.now() + timedelta(minutes=30),
            )
        )
    for _ in range(MAX_QUEUED_RUNS - 1):
        queue_run(factory, storage)

    accepted = queue_run(factory, storage)

    assert accepted.replayed is False


def test_duas_requisicoes_simultaneas_nao_passam_da_decima_vaga(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    for _ in range(MAX_QUEUED_RUNS - 1):
        queue_run(factory, storage)
    owners = [new_owner(factory) for _ in range(2)]
    commands = {o: run_command(ready_inputs(factory, storage, o)) for o in owners}
    pending = list(owners)
    lock = threading.Lock()

    def next_request() -> Accepted:
        with lock:
            owner = pending.pop()
        return accept(factory, owner, commands[owner])

    outcomes = run_concurrently(2, next_request)

    assert sorted(type(o).__name__ for o in outcomes) == ["Accepted", "QueueFullError"]
    with factory() as session:
        queued = session.execute(select(func.count()).where(run.c.status == "queued")).scalar_one()
    assert queued == MAX_QUEUED_RUNS


def test_fila_travada_alem_do_lock_timeout_responde_fila_cheia(
    engine: Engine, db_settings: Settings, storage: FileStorage
) -> None:
    impatient = make_engine(db_settings.model_copy(update={"db_lock_timeout_ms": 100}))
    factory = make_session_factory(impatient)
    owner = new_owner(factory)
    command = run_command(ready_inputs(factory, storage, owner))
    try:
        with engine.connect() as blocker:
            blocker.execute(
                select(func.pg_advisory_xact_lock(ADVISORY_CLASS_QUEUE, QUEUE_LOCK_OBJECT))
            )
            with pytest.raises(QueueFullError):
                accept(factory, owner, command)
            blocker.rollback()
        assert count_rows(factory)["run"] == 0
    finally:
        impatient.dispose()


def test_upload_repetido_e_recusado(factory: sessionmaker[Session], storage: FileStorage) -> None:
    owner = new_owner(factory)
    [upload_id] = ready_inputs(factory, storage, owner)

    with pytest.raises(InvalidRunCommandError) as caught:
        accept(factory, owner, run_command([upload_id, upload_id]))

    assert [e.location for e in caught.value.errors] == ["input_upload_ids"]


def test_cinco_tabelas_sao_aceitas(factory: sessionmaker[Session], storage: FileStorage) -> None:
    owner = new_owner(factory)

    accepted = accept(
        factory, owner, run_command(ready_inputs(factory, storage, owner, CSV_MAX_INPUTS))
    )

    assert accepted.replayed is False


def test_seis_tabelas_sao_recusadas(factory: sessionmaker[Session], storage: FileStorage) -> None:
    owner = new_owner(factory)
    inputs = ready_inputs(factory, storage, owner, CSV_MAX_INPUTS + 1)

    with pytest.raises(InvalidRunCommandError) as caught:
        accept(factory, owner, run_command(inputs))

    assert [e.location for e in caught.value.errors] == ["input_upload_ids"]
    assert count_rows(factory)["run"] == 0


def test_lista_vazia_de_uploads_e_recusada(factory: sessionmaker[Session]) -> None:
    owner = new_owner(factory)

    with pytest.raises(InvalidRunCommandError) as caught:
        accept(factory, owner, run_command([]))

    assert [e.location for e in caught.value.errors] == ["input_upload_ids"]


@pytest.mark.parametrize("reason", ["inexistente", "alheio", "nao-pronto", "excluido", "vencido"])
def test_upload_indisponivel_e_recusado_sem_criar_nada(
    factory: sessionmaker[Session], storage: FileStorage, reason: str
) -> None:
    owner = new_owner(factory)
    [good] = ready_inputs(factory, storage, owner)
    bad = unavailable_upload(factory, storage, owner, reason)
    before = count_rows(factory)

    with pytest.raises(InputNotAvailableError):
        accept(factory, owner, run_command([good, bad]))

    assert count_rows(factory) == before


def test_parametro_invalido_aponta_o_campo_dentro_de_parameters(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = new_owner(factory)
    inputs = ready_inputs(factory, storage, owner)

    with pytest.raises(InvalidRunCommandError) as caught:
        accept(factory, owner, run_command(inputs, {"formula_guard": "talvez"}))

    assert [e.location for e in caught.value.errors] == ["parameters.formula_guard"]


def test_campo_desconhecido_nos_parametros_e_recusado() -> None:
    with pytest.raises(InvalidRunCommandError) as caught:
        validate_run(run_command([], {"desconhecido": 1}))

    assert [e.location for e in caught.value.errors] == ["parameters.desconhecido"]


def test_versao_desconhecida_e_recusada() -> None:
    command = replace(run_command([]), recipe_version=99)

    with pytest.raises(InvalidRunCommandError) as caught:
        validate_run(command)

    assert [e.location for e in caught.value.errors] == ["recipe_version"]


def test_versao_retirada_e_recusada(monkeypatch: pytest.MonkeyPatch) -> None:
    retired = replace(CSV_JANITOR_V1, status=VersionStatus.RETIRED)
    monkeypatch.setattr(acceptance, "CATALOG", Catalog([retired]))

    with pytest.raises(RecipeVersionRetiredError):
        validate_run(run_command([]))


def test_validacao_aplica_os_defaults_sem_exigir_uploads() -> None:
    validated = validate_run(run_command([], {"trim_columns": ["a"]}))

    assert validated.input_upload_ids == ()
    assert validated.parameters["trim_columns"] == ["a"]
    assert validated.parameters["output_columns"] is None
    assert list(validated.parameters) == sorted(validated.parameters)
