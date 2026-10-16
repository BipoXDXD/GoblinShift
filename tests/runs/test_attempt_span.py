"""Span `attempt` de ponta a ponta: PostgreSQL e executor reais (docs/GoblinShiftPlan.md, 3.7).

Um span `main` por tentativa, ligado por link ao trace da aceitação gravado na Run, com as fases
medidas pelo executor e pelo worker. Nunca nomes de arquivo nem parâmetros.
"""

import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from sqlalchemy import update
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.files.storage import FileStorage
from goblinshift.platform.db import unit_of_work
from goblinshift.platform.telemetry import UserHasher
from goblinshift.runs.tables import run
from goblinshift.runs.worker.coordinator import Coordinator
from goblinshift.runs.worker.runner import Runner
from tests.runs.worker_support import (
    INVALID,
    VALID,
    attempts,
    claim,
    coordinator_config,
    create_user,
    local_runner,
    queue_run,
    run_row,
)

pytestmark = pytest.mark.integration

HASHER = UserHasher(b"chave-de-teste-do-user-hash-com-32-caracteres")
TRACE_ID = 0x4BF92F3577B34DA6A3CE929D0E0E4736
SPAN_ID = 0x00F067AA0BA902B7
TRACEPARENT = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
FILE_NAME = "clientes-CANARIO.csv"


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
    claimed = claim(factory, storage)

    traced_coordinator(factory, storage, local_runner(work_root), exporter).run(claimed)

    span = only_attempt_span(exporter)
    attributes = dict(span.attributes or {})
    [attempt_row] = attempts(factory, run_id)
    assert span.parent is None
    [link] = span.links
    assert (link.context.trace_id, link.context.span_id) == (TRACE_ID, SPAN_ID)
    timing_keys = {"inspect_ms", "process_ms", "package_ms", "collect_ms", "publish_ms"}
    assert {key: attributes[key] for key in attributes.keys() - timing_keys - {"duration_ms"}} == {
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
    }
    for key in (*timing_keys, "duration_ms"):
        value = attributes[key]
        assert isinstance(value, int)
        assert value >= 0
    text = repr(attributes)
    assert "CANARIO" not in text
    assert str(owner) not in text


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


def test_sem_tracer_o_coordenador_funciona_igual(
    factory: sessionmaker[Session], storage: FileStorage, work_root: Path
) -> None:
    run_id = queue_run(factory, storage, create_user(factory))
    claimed = claim(factory, storage)

    Coordinator(factory, storage, local_runner(work_root), coordinator_config()).run(claimed)

    assert run_row(factory, run_id)["status"] == "succeeded"
