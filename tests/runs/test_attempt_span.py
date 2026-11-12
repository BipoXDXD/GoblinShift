"""Span `attempt` de ponta a ponta: PostgreSQL e executor reais (docs/GoblinShiftPlan.md, 3.7).

Um span `main` por tentativa, ligado por link ao trace da aceitação gravado na Run, com as fases
medidas pelo executor e pelo worker. Nunca nomes de arquivo nem parâmetros.
"""

import errno
import logging
import os
import uuid
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from sqlalchemy import func, update
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.files.artifacts import create_candidate_object
from goblinshift.files.storage import FileStorage, StorageError
from goblinshift.platform.db import unit_of_work
from goblinshift.platform.telemetry import AlertEventCounter, UserHasher
from goblinshift.runs.cancellation import cancel_run
from goblinshift.runs.tables import run
from goblinshift.runs.worker.claim import claim_next
from goblinshift.runs.worker.coordinator import Coordinator
from goblinshift.runs.worker.executor_ctl_runner import ExecutorCtlRunner
from goblinshift.runs.worker.runner import LocalRunner, Runner
from tests.runs.fake_executor_ctl import IMAGE, FakeExecutorCtl
from tests.runs.test_input_tampered import plant_symlink_over_stored_object
from tests.runs.worker_support import (
    INVALID,
    LEASE,
    VALID,
    attempts,
    claim,
    coordinator_config,
    create_user,
    local_runner,
    make_eligible_now,
    queue_run,
    run_row,
)

pytestmark = pytest.mark.integration

HASHER = UserHasher(b"chave-de-teste-do-user-hash-com-32-caracteres")
TRACE_ID = 0x4BF92F3577B34DA6A3CE929D0E0E4736
SPAN_ID = 0x00F067AA0BA902B7
TRACEPARENT = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
FILE_NAME = "clientes-CANARIO.csv"
COORDINATOR_LOGGER = "goblinshift.runs.worker.coordinator"


@pytest.fixture
def exporter() -> Iterator[InMemorySpanExporter]:
    exporter = InMemorySpanExporter()
    yield exporter
    exporter.clear()


def traced_coordinator(
    factory: sessionmaker[Session],
    storage: FileStorage,
    runner: Runner,
    exporter: InMemorySpanExporter,
) -> Coordinator:
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return Coordinator(
        factory,
        storage,
        runner,
        coordinator_config(),
        tracer=provider.get_tracer("teste"),
        user_hasher=HASHER,
    )


def set_traceparent(factory: sessionmaker[Session], run_id: uuid.UUID, value: str | None) -> None:
    with unit_of_work(factory) as session:
        session.execute(update(run).where(run.c.id == run_id).values(traceparent=value))


def only_attempt_span(exporter: InMemorySpanExporter) -> ReadableSpan:
    [span] = [s for s in exporter.get_finished_spans() if s.name == "attempt"]
    return span


def test_tentativa_publicada_tem_o_evento_completo_e_link_para_a_aceitacao(
    factory: sessionmaker[Session],
    storage: FileStorage,
    work_root: Path,
    exporter: InMemorySpanExporter,
) -> None:
    owner = create_user(factory)
    run_id = queue_run(factory, storage, owner, ((FILE_NAME, VALID), ("ruim.csv", INVALID)))
    set_traceparent(factory, run_id, TRACEPARENT)
    eligible_at = make_eligible_since(factory, run_id, timedelta(seconds=90))
    claimed = claim(factory, storage)

    traced_coordinator(factory, storage, local_runner(work_root), exporter).run(claimed)

    span = only_attempt_span(exporter)
    attributes = dict(span.attributes or {})
    [attempt_row] = attempts(factory, run_id)
    assert span.parent is None
    [link] = span.links
    assert (link.context.trace_id, link.context.span_id) == (TRACE_ID, SPAN_ID)
    timing_keys = {"inspect_ms", "process_ms", "package_ms", "collect_ms", "publish_ms"}
    measured = timing_keys | {"duration_ms", "queue_wait_ms"}
    assert {key: attributes[key] for key in attributes.keys() - measured} == {
        "main": True,
        "run_id": str(run_id),
        "attempt_id": str(attempt_row["id"]),
        "attempt.number": 1,
        "attempt.generation": 1,
        "recipe_id": "csv-janitor",
        "recipe_version": 1,
        "user.hash": HASHER.hash(owner),
        "attempt.inputs": 2,
        "attempt.outcome": "succeeded_with_warnings",
        "executor.exit_code": 0,
        "executor.oom_killed": False,
        "executor.timed_out": False,
        "executor.enospc": False,
        "attempt.outputs": 1,
        "attempt.output_bytes": len(VALID),
        "attempt.rejected": 1,
        "attempt.rejected.CSV_STRUCTURE_INVALID": 1,
    }
    # Da elegibilidade ao início da tentativa, os dois pelo relógio do banco.
    wait_ms = (attempt_row["started_at"] - eligible_at) // timedelta(milliseconds=1)
    assert wait_ms >= 90_000
    assert attributes["queue_wait_ms"] == wait_ms
    for key in (*timing_keys, "duration_ms"):
        value = attributes[key]
        assert isinstance(value, int)
        assert value >= 0
    text = repr(attributes)
    assert "CANARIO" not in text
    assert str(owner) not in text


def make_eligible_since(
    factory: sessionmaker[Session], run_id: uuid.UUID, ago: timedelta
) -> datetime:
    with unit_of_work(factory) as session:
        eligible_at: datetime = session.execute(
            update(run)
            .where(run.c.id == run_id)
            .values(eligible_at=func.now() - ago)
            .returning(run.c.eligible_at)
        ).scalar_one()
    return eligible_at


def test_sem_traceparent_na_run_o_span_fica_sem_link(
    factory: sessionmaker[Session],
    storage: FileStorage,
    work_root: Path,
    exporter: InMemorySpanExporter,
) -> None:
    run_id = queue_run(factory, storage, create_user(factory))
    set_traceparent(factory, run_id, None)
    claimed = claim(factory, storage)

    traced_coordinator(factory, storage, local_runner(work_root), exporter).run(claimed)

    span = only_attempt_span(exporter)
    assert span.links == ()
    assert span.attributes is not None
    assert span.attributes["attempt.outcome"] == "succeeded"
    assert "attempt.rejected.CSV_STRUCTURE_INVALID" not in span.attributes
    assert "attempt.stop_reason" not in span.attributes
    assert "attempt.cancel_latency_ms" not in span.attributes


def test_traceparent_invalido_nao_derruba_a_tentativa(
    factory: sessionmaker[Session],
    storage: FileStorage,
    work_root: Path,
    exporter: InMemorySpanExporter,
) -> None:
    run_id = queue_run(factory, storage, create_user(factory))
    set_traceparent(factory, run_id, "lixo-que-nao-e-traceparent")
    claimed = claim(factory, storage)

    traced_coordinator(factory, storage, local_runner(work_root), exporter).run(claimed)

    assert only_attempt_span(exporter).links == ()
    assert run_row(factory, run_id)["status"] == "succeeded"


def test_disco_cheio_no_executor_e_limite_de_recurso_deterministico(
    factory: sessionmaker[Session],
    storage: FileStorage,
    work_root: Path,
    exporter: InMemorySpanExporter,
) -> None:
    run_id = queue_run(factory, storage, create_user(factory))
    claimed = claim(factory, storage)
    runner = local_runner(work_root, "exit", "6")

    traced_coordinator(factory, storage, runner, exporter).run(claimed)

    row = run_row(factory, run_id)
    assert (row["status"], row["error_code"]) == ("failed", "RESOURCE_LIMIT_EXCEEDED")
    assert attempts(factory, run_id)[0]["error_code"] == "RESOURCE_LIMIT_EXCEEDED"
    attributes = only_attempt_span(exporter).attributes
    assert attributes is not None
    assert attributes["executor.enospc"] is True
    assert attributes["executor.exit_code"] == 6
    assert attributes["error.code"] == "RESOURCE_LIMIT_EXCEEDED"
    assert attributes["attempt.outcome"] == "failed"
    # O executor de teste não gravou telemetry.json: os tempos dele ficam de fora.
    assert "inspect_ms" not in attributes
    assert "collect_ms" not in attributes


def test_candidato_preso_no_disco_conta_no_evento_da_tentativa(
    factory: sessionmaker[Session],
    storage: FileStorage,
    work_root: Path,
    exporter: InMemorySpanExporter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = create_user(factory)
    run_id = queue_run(factory, storage, owner)
    claimed = claim(factory, storage)
    with unit_of_work(factory) as session:
        candidate = create_candidate_object(
            session, owner, run_id, claimed.attempt_id, claimed.generation
        )
    storage.create_exclusive(candidate.storage_key, [b"x"], 1)
    delete = storage.delete

    def refuse_candidate(key: str) -> None:
        if key == candidate.storage_key:
            raise PermissionError("disco recusou")
        delete(key)

    monkeypatch.setattr(storage, "delete", refuse_candidate)

    traced_coordinator(factory, storage, local_runner(work_root, "exit", "6"), exporter).run(
        claimed
    )

    assert run_row(factory, run_id)["status"] == "failed"
    attributes = only_attempt_span(exporter).attributes
    assert attributes is not None
    assert attributes["attempt.candidates_left_on_disk"] == 1


@pytest.fixture
def counting_alert_events() -> Iterator[None]:
    coordinator_logger = logging.getLogger(COORDINATOR_LOGGER)
    handler = AlertEventCounter()
    coordinator_logger.addHandler(handler)
    yield
    coordinator_logger.removeHandler(handler)


@pytest.mark.usefixtures("counting_alert_events")
def test_executor_que_nao_inicia_conta_em_cada_tentativa_ate_o_infra_poison(
    factory: sessionmaker[Session],
    storage: FileStorage,
    tmp_path: Path,
    exporter: InMemorySpanExporter,
) -> None:
    # Imagem fora do daemon: o `--pull=never` faz o executor-ctl responder 503 a todo start.
    area = tmp_path / "exec-work"
    area.mkdir()
    ctl = FakeExecutorCtl(area, docker_down=True)
    runner = ExecutorCtlRunner(ctl.socket_path, area, work_group=os.getgid())
    run_id = queue_run(factory, storage, create_user(factory), executor_image=IMAGE)
    coordinator = traced_coordinator(factory, storage, runner, exporter)

    try:
        for _ in range(4):
            coordinator.run(claim(factory, storage))
            make_eligible_now(factory, run_id)
        assert claim_next(factory, storage, LEASE) is None
    finally:
        ctl.close()

    row = run_row(factory, run_id)
    assert (row["status"], row["error_code"]) == ("failed", "INFRA_POISON")
    counts = [
        dict(span.attributes or {}).get("alert.executor_start_failed")
        for span in exporter.get_finished_spans()
        if span.name == "attempt"
    ]
    assert counts == [1, 1, 1, 1]


def assert_ended_without_refused_start(exporter: InMemorySpanExporter, outcome: str) -> None:
    attributes = dict(only_attempt_span(exporter).attributes or {})
    assert attributes["attempt.outcome"] == outcome
    assert "alert.executor_start_failed" not in attributes


@pytest.mark.usefixtures("counting_alert_events")
def test_start_bem_sucedido_nao_conta_como_start_recusado(
    factory: sessionmaker[Session],
    storage: FileStorage,
    work_root: Path,
    exporter: InMemorySpanExporter,
) -> None:
    queue_run(factory, storage, create_user(factory))

    traced_coordinator(factory, storage, local_runner(work_root), exporter).run(
        claim(factory, storage)
    )

    assert_ended_without_refused_start(exporter, "succeeded")


@pytest.mark.usefixtures("counting_alert_events")
def test_cancelamento_antes_do_start_nao_conta_como_start_recusado(
    factory: sessionmaker[Session],
    storage: FileStorage,
    work_root: Path,
    exporter: InMemorySpanExporter,
) -> None:
    owner = create_user(factory)
    run_id = queue_run(factory, storage, owner)
    claimed = claim(factory, storage)
    cancel_run(factory, storage, owner, run_id)

    traced_coordinator(factory, storage, local_runner(work_root), exporter).run(claimed)

    assert_ended_without_refused_start(exporter, "canceled")


@pytest.mark.usefixtures("counting_alert_events")
def test_preparacao_que_falha_nao_conta_como_start_recusado(
    factory: sessionmaker[Session],
    storage: FileStorage,
    work_root: Path,
    exporter: InMemorySpanExporter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queue_run(factory, storage, create_user(factory))
    claimed = claim(factory, storage)

    def refuse_open(_key: str) -> None:
        raise StorageError("storage recusou")

    monkeypatch.setattr(storage, "open", refuse_open)

    traced_coordinator(factory, storage, local_runner(work_root), exporter).run(claimed)

    assert_ended_without_refused_start(exporter, "running")


@pytest.mark.usefixtures("counting_alert_events")
def test_leitura_da_entrada_que_falha_no_disco_nao_conta_como_start_recusado(
    factory: sessionmaker[Session],
    storage: FileStorage,
    work_root: Path,
    exporter: InMemorySpanExporter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # O `OSError` cru vem do storage, não da área de trabalho: não é o executor que não inicia.
    queue_run(factory, storage, create_user(factory))
    claimed = claim(factory, storage)

    def refuse_open(key: str) -> None:
        raise PermissionError(errno.EACCES, "sem permissão", key)

    monkeypatch.setattr(storage, "open", refuse_open)

    traced_coordinator(factory, storage, local_runner(work_root), exporter).run(claimed)

    assert_ended_without_refused_start(exporter, "running")


class _WorkAreaRefused(LocalRunner):
    """Área sem escrita (montagem perdida, por exemplo): o executor nem chega ao `start`."""

    def create_work_dir(self, attempt_id: uuid.UUID) -> Path:
        raise PermissionError(errno.EACCES, "sem permissão", str(self.work_dir(attempt_id)))


@pytest.mark.usefixtures("counting_alert_events")
def test_entrada_adulterada_conta_no_span_sem_virar_start_recusado(
    factory: sessionmaker[Session],
    storage: FileStorage,
    work_root: Path,
    tmp_path: Path,
    exporter: InMemorySpanExporter,
) -> None:
    # `gs-causa-entrada-adulterada` lê `alert.input_tampered` no span da tentativa, e
    # `gs-causa-executor-nao-iniciou` não pode contar a mesma Run.
    queue_run(factory, storage, create_user(factory))
    plant_symlink_over_stored_object(storage, tmp_path / "alvo-fora-da-raiz")
    claimed = claim(factory, storage)

    traced_coordinator(factory, storage, local_runner(work_root), exporter).run(claimed)

    attributes = dict(only_attempt_span(exporter).attributes or {})
    assert attributes["alert.input_tampered"] == 1
    assert "alert.executor_start_failed" not in attributes


@pytest.mark.usefixtures("counting_alert_events")
def test_area_de_trabalho_que_nao_e_criada_dispara_o_alerta_do_executor(
    factory: sessionmaker[Session],
    storage: FileStorage,
    work_root: Path,
    exporter: InMemorySpanExporter,
    caplog: pytest.LogCaptureFixture,
) -> None:
    queue_run(factory, storage, create_user(factory))
    claimed = claim(factory, storage)
    runner = _WorkAreaRefused(work_root, attempt_timeout=timedelta(seconds=30))

    with caplog.at_level(logging.WARNING, logger=COORDINATOR_LOGGER):
        traced_coordinator(factory, storage, runner, exporter).run(claimed)

    attributes = dict(only_attempt_span(exporter).attributes or {})
    assert attributes["attempt.outcome"] == "running"
    assert attributes["alert.executor_start_failed"] == 1
    [alerted] = [r for r in caplog.records if "alert" in getattr(r, "fields", {})]
    fields = vars(alerted)["fields"]
    assert fields["alert"] == "executor_start_failed"
    assert fields.keys() == {
        "run_id",
        "attempt_id",
        "generation",
        "attempt.number",
        "error",
        "alert",
    }


def test_sem_tracer_o_coordenador_funciona_igual(
    factory: sessionmaker[Session], storage: FileStorage, work_root: Path
) -> None:
    run_id = queue_run(factory, storage, create_user(factory))
    claimed = claim(factory, storage)

    Coordinator(factory, storage, local_runner(work_root), coordinator_config()).run(claimed)

    assert run_row(factory, run_id)["status"] == "succeeded"


def test_cancelamento_antes_do_inicio_traz_a_razao_e_a_latencia(
    factory: sessionmaker[Session],
    storage: FileStorage,
    work_root: Path,
    exporter: InMemorySpanExporter,
) -> None:
    owner = create_user(factory)
    run_id = queue_run(factory, storage, owner)
    claimed = claim(factory, storage)
    cancel_run(factory, storage, owner, run_id)

    traced_coordinator(factory, storage, local_runner(work_root), exporter).run(claimed)

    row = run_row(factory, run_id)
    assert row["status"] == "canceled"
    attributes = only_attempt_span(exporter).attributes
    assert attributes is not None
    assert attributes["attempt.stop_reason"] == "CANCELED"
    # A confirmação da parada é a transação que torna a Run terminal: `finished_at`.
    latency = row["finished_at"] - row["cancel_requested_at"]
    assert attributes["attempt.cancel_latency_ms"] == latency // timedelta(milliseconds=1)
    assert attributes["attempt.outcome"] == "canceled"


def test_parada_pelo_prazo_traz_a_razao_sem_latencia_de_cancelamento(
    factory: sessionmaker[Session],
    storage: FileStorage,
    work_root: Path,
    exporter: InMemorySpanExporter,
) -> None:
    run_id = queue_run(factory, storage, create_user(factory))
    claimed = claim(factory, storage)
    # Prazo vencido gravado como a renovação do lease o gravaria, antes do início do executor.
    with unit_of_work(factory) as session:
        session.execute(
            update(run)
            .where(run.c.id == run_id)
            .values(status="stopping", phase=None, stop_reason="DEADLINE_EXCEEDED")
        )

    traced_coordinator(factory, storage, local_runner(work_root), exporter).run(claimed)

    assert (run_row(factory, run_id)["error_code"]) == "DEADLINE_EXCEEDED"
    attributes = only_attempt_span(exporter).attributes
    assert attributes is not None
    assert attributes["attempt.stop_reason"] == "DEADLINE_EXCEEDED"
    assert "attempt.cancel_latency_ms" not in attributes
