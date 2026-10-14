"""Span `run.finished` emitido onde a Run chega ao estado terminal (3.7), com PostgreSQL real.

Worker (publicação e INFRA_POISON no claim) e manutenção (fila e prazo vencidos) passam pelo
mesmo ponto depois do commit; transições que não terminam a Run não emitem nada.
"""

import uuid
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from sqlalchemy import func, update
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.files.storage import FileStorage
from goblinshift.platform.db import unit_of_work
from goblinshift.platform.telemetry import UserHasher
from goblinshift.runs.finished import SPAN_NAME, RunFinishedReporter
from goblinshift.runs.lifecycle import MIN_RETRY_JITTER
from goblinshift.runs.maintenance import Maintenance, MaintenanceConfig
from goblinshift.runs.tables import run
from goblinshift.runs.worker.claim import claim_next
from goblinshift.runs.worker.coordinator import Coordinator
from tests.runs.worker_support import (
    LEASE,
    VALID,
    coordinator_config,
    create_user,
    expire_lease,
    local_runner,
    make_eligible_now,
    queue_run,
)

pytestmark = pytest.mark.integration

HASHER = UserHasher(b"chave-de-teste-do-user-hash-com-32-caracteres")


class Spans:
    def __init__(self, factory: sessionmaker[Session]) -> None:
        self.exporter = InMemorySpanExporter()
        self.provider = TracerProvider()
        self.provider.add_span_processor(SimpleSpanProcessor(self.exporter))
        self.reporter = RunFinishedReporter(factory, HASHER, self.provider.get_tracer("test"))

    def finished(self) -> list[ReadableSpan]:
        return [span for span in self.exporter.get_finished_spans() if span.name == SPAN_NAME]

    def only_event(self) -> dict[str, Any]:
        [span] = self.finished()
        return dict(span.attributes or {})


@pytest.fixture
def spans(factory: sessionmaker[Session]) -> Iterator[Spans]:
    traced = Spans(factory)
    yield traced
    traced.provider.shutdown()


def maintenance(
    factory: sessionmaker[Session], storage: FileStorage, work_root: Path, spans: Spans
) -> Maintenance:
    config = MaintenanceConfig(stop_grace=timedelta(seconds=2), jitter=lambda: MIN_RETRY_JITTER)
    return Maintenance(
        factory, storage, local_runner(work_root), config, on_finished=spans.reporter.report
    )


def set_run(factory: sessionmaker[Session], run_id: uuid.UUID, **values: object) -> None:
    with unit_of_work(factory) as session:
        session.execute(update(run).where(run.c.id == run_id).values(**values))


def test_publicacao_pelo_worker_emite_o_termino_elegivel_e_bom(
    factory: sessionmaker[Session], storage: FileStorage, work_root: Path, spans: Spans
) -> None:
    owner = create_user(factory)
    run_id = queue_run(factory, storage, owner)
    worker = Coordinator(
        factory,
        storage,
        local_runner(work_root),
        coordinator_config(),
        on_finished=spans.reporter.report,
    )
    claimed = claim_next(factory, storage, LEASE, on_finished=spans.reporter.report)
    assert claimed is not None

    worker.run(claimed)

    event = spans.only_event()
    assert event["main"] is True
    assert event["run_id"] == str(run_id)
    assert event["user.hash"] == HASHER.hash(owner)
    assert event["run.state"] == "succeeded"
    assert "error.code" not in event
    assert event["run.attempts"] == 1
    assert event["run.input_count"] == 1
    assert event["run.duration_ms"] >= 0
    assert event["run.queue_wait_ms"] >= 0
    assert event["slo.execution.eligible"] is True


def test_fila_vencida_pela_manutencao_emite_queue_expired_sem_duracao(
    factory: sessionmaker[Session], storage: FileStorage, work_root: Path, spans: Spans
) -> None:
    run_id = queue_run(factory, storage, create_user(factory), (("a.csv", VALID), ("b.csv", VALID)))
    set_run(factory, run_id, queue_expires_at=func.now() - timedelta(seconds=1))

    maintenance(factory, storage, work_root, spans).run_cycle()

    event = spans.only_event()
    assert (event["run.state"], event["error.code"]) == ("failed", "QUEUE_EXPIRED")
    assert event["run.input_count"] == 2
    assert event["run.attempts"] == 0
    assert "run.duration_ms" not in event
    assert event["slo.execution.eligible"] is True


def test_prazo_vencido_pela_manutencao_emite_deadline_exceeded_com_duracao(
    factory: sessionmaker[Session], storage: FileStorage, work_root: Path, spans: Spans
) -> None:
    run_id = queue_run(factory, storage, create_user(factory))
    janitor = maintenance(factory, storage, work_root, spans)
    claim_next(factory, storage, LEASE, on_finished=spans.reporter.report)
    expire_lease(factory, run_id)
    janitor.run_cycle()
    assert spans.finished() == []
    set_run(factory, run_id, deadline_at=func.now() - timedelta(seconds=1))

    janitor.run_cycle()

    event = spans.only_event()
    assert (event["run.state"], event["error.code"]) == ("failed", "DEADLINE_EXCEEDED")
    assert event["run.duration_ms"] >= 0


def test_quinto_claim_emite_infra_poison_uma_vez(
    factory: sessionmaker[Session], storage: FileStorage, work_root: Path, spans: Spans
) -> None:
    run_id = queue_run(factory, storage, create_user(factory))
    janitor = maintenance(factory, storage, work_root, spans)
    for _ in range(4):
        claim_next(factory, storage, LEASE, on_finished=spans.reporter.report)
        expire_lease(factory, run_id)
        janitor.run_cycle()
        make_eligible_now(factory, run_id)

    assert claim_next(factory, storage, LEASE, on_finished=spans.reporter.report) is None

    event = spans.only_event()
    assert (event["run.state"], event["error.code"]) == ("failed", "INFRA_POISON")
    assert event["run.attempts"] == 4


def test_falha_ao_montar_o_evento_nao_interrompe_quem_concluiu_a_run(
    spans: Spans, caplog: pytest.LogCaptureFixture
) -> None:
    # A Run já está terminal e com os usos liberados: perder o evento custa só telemetria.
    missing = uuid.uuid4()

    spans.reporter.report(missing)

    assert spans.finished() == []
    assert any(str(missing) in str(getattr(r, "fields", "")) for r in caplog.records)
