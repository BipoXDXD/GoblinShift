"""Composição comum de `gs-worker`, `gs-maintenance` e `gs-admin`: o Runner da configuração, o
tracer do processo e o emissor do `run.finished` (3.4, 3.7).
"""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta

from opentelemetry.trace import NoOpTracer, Tracer
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.platform.settings import Settings
from goblinshift.platform.telemetry import (
    UserHasher,
    make_tracer_provider,
    shut_down_tracer_provider,
)
from goblinshift.runs.finished import RunFinishedReporter
from goblinshift.runs.worker.executor_ctl_runner import ExecutorCtlRunner
from goblinshift.runs.worker.runner import LocalRunner, Runner

# O escopo dos spans de worker, manutenção e `gs-admin` desde sempre, quando o tracer era pedido
# com o `__name__` de `entrypoints/worker.py`. Trocar muda o `scope.name` exportado.
TRACER_SCOPE = "goblinshift.entrypoints.worker"


def build_runner(settings: Settings) -> Runner:
    """Runner da configuração; `ExecutorConfigError` se ela não serve ao ambiente."""
    settings.executor_image_for_runs()
    if settings.runner == "executor_ctl":
        return ExecutorCtlRunner(
            settings.executor_ctl_socket, settings.work_root, work_group=settings.executor_group_id
        )
    return LocalRunner(
        settings.work_root, attempt_timeout=timedelta(seconds=settings.attempt_timeout_s)
    )


@contextmanager
def process_tracer(settings: Settings, default_service: str) -> Iterator[Tracer]:
    """Tracer dos eventos amplos do processo (3.7); ao sair, exporta o lote pendente com prazo.

    Sem endpoint OTLP (desenvolvimento e testes), um tracer que não grava.
    """
    provider = make_tracer_provider(settings, default_service)
    try:
        yield provider.get_tracer(TRACER_SCOPE) if provider is not None else NoOpTracer()
    finally:
        shut_down_tracer_provider(provider)


@contextmanager
def run_finished_reporter(
    settings: Settings, factory: sessionmaker[Session], default_service: str
) -> Iterator[RunFinishedReporter]:
    """Emissor do `run.finished` (3.7) do processo, com o próprio `process_tracer`."""
    hasher = UserHasher(settings.user_hash_key())
    with process_tracer(settings, default_service) as tracer:
        yield RunFinishedReporter(factory, hasher, tracer)
