"""Processo `gs-maintenance`: reconciliação e retenção em ciclos (docs/GoblinShiftPlan.md, 3.4).

Um ciclo a cada `GS_MAINTENANCE_INTERVAL_S` (30 s): o de execuções e arquivos (`Maintenance`) e o
de contas (`AccountJobs`: purga das contas em exclusão, retenção da identidade, 1.8, e limpeza das
janelas do limite de requisições, ADR 028). Cada volta é um span `maintenance.cycle` com
`main = true` (3.7), com as contagens dos dois ciclos, que os alertas de causa de
`deploy/openobserve` leem; cada ciclo também registra seu evento de log. A falha de um ciclo é
registrada e o seguinte tenta de novo. Os lotes usam
`statement_timeout` de `GS_MAINTENANCE_STATEMENT_TIMEOUT_MS` (30 s, 3.2).

SIGTERM e SIGINT terminam o ciclo em andamento e saem.
"""

import logging
import signal
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from types import FrameType
from uuid import UUID

from opentelemetry.trace import NoOpTracer, Status, StatusCode, Tracer
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.entrypoints.worker import build_runner, run_finished_reporter
from goblinshift.files import purge as files_purge
from goblinshift.files.storage import FileStorage
from goblinshift.identity import purge as identity_purge
from goblinshift.identity import rate_limit
from goblinshift.platform.db import make_engine, make_session_factory, unit_of_work
from goblinshift.platform.settings import ExecutorConfigError, MissingSecretError, Settings
from goblinshift.platform.telemetry import configure_logging, make_tracer_provider
from goblinshift.runs import purge as runs_purge
from goblinshift.runs.maintenance import Maintenance, MaintenanceConfig
from goblinshift.runs.worker.runner import Runner

logger = logging.getLogger("goblinshift.maintenance")

EXIT_OK = 0
EXIT_CONFIGURATION = 2

type _Clock = Callable[[], datetime]
type Cycle = tuple[str, Callable[[], Mapping[str, int]]]

CYCLE_SPAN = "maintenance.cycle"
_ATTRIBUTE_PREFIX = "maintenance."


def main() -> int:
    configure_logging()
    settings = Settings()
    try:
        runner = build_runner(settings)
        settings.user_hash_key()
    except (ExecutorConfigError, MissingSecretError) as error:
        logger.error("manutenção não sobe: %s", error)
        return EXIT_CONFIGURATION
    shutdown = threading.Event()

    def request_shutdown(signum: int, _frame: FrameType | None) -> None:
        logger.info("parada pedida", extra={"fields": {"signal": signum}})
        shutdown.set()

    signal.signal(signal.SIGTERM, request_shutdown)
    signal.signal(signal.SIGINT, request_shutdown)
    serve(settings, runner, shutdown)
    return EXIT_OK


def serve(settings: Settings, runner: Runner, shutdown: threading.Event) -> None:
    batch_settings = settings.model_copy(
        update={"db_statement_timeout_ms": settings.maintenance_statement_timeout_ms}
    )
    engine = make_engine(batch_settings)
    factory = make_session_factory(engine)
    provider = make_tracer_provider(settings, "goblinshift-maintenance")
    tracer: Tracer = provider.get_tracer(__name__) if provider is not None else NoOpTracer()
    try:
        with run_finished_reporter(settings, factory, "goblinshift-maintenance") as finished:
            maintenance = Maintenance(
                factory,
                FileStorage(settings.storage_root),
                runner,
                MaintenanceConfig(missing_max_absent_ratio=settings.missing_sweep_max_absent_ratio),
                on_finished=finished.report,
            )
            account_jobs = AccountJobs(factory)
            logger.info("manutenção pronta")
            cycles: tuple[Cycle, ...] = (
                ("execuções", maintenance.run_cycle),
                ("contas", account_jobs.run_cycle),
            )
            while not shutdown.is_set():
                run_cycles(tracer, cycles)
                shutdown.wait(settings.maintenance_interval_s)
    finally:
        engine.dispose()
        if provider is not None:
            provider.shutdown()
    logger.info("manutenção encerrada")


def run_cycles(tracer: Tracer, cycles: Sequence[Cycle]) -> None:
    """Uma volta: roda os ciclos em sequência dentro de um span `maintenance.cycle`.

    Ciclos independentes: a falha de um não impede o outro. O span leva as contagens de todos,
    com o prefixo `maintenance.`, e quantos falharam; a mensagem da exceção fica só no log, porque
    pode trazer valores do banco (3.7).
    """
    started = time.monotonic()
    with tracer.start_as_current_span(
        CYCLE_SPAN, record_exception=False, set_status_on_exception=False
    ) as span:
        failed = 0
        for name, cycle in cycles:
            try:
                counts = cycle()
            except Exception:
                # Fronteira do processo: o ciclo seguinte recomeça do banco, sem estado em memória.
                logger.exception("ciclo de manutenção falhou", extra={"fields": {"cycle": name}})
                failed += 1
                continue
            for key, value in counts.items():
                span.set_attribute(f"{_ATTRIBUTE_PREFIX}{key}", value)
        span.set_attribute("main", True)
        span.set_attribute(f"{_ATTRIBUTE_PREFIX}failed_cycles", failed)
        span.set_attribute("duration_ms", round((time.monotonic() - started) * 1000))
        if failed:
            span.set_status(Status(StatusCode.ERROR, "ciclo de manutenção falhou"))


@dataclass(frozen=True)
class AccountJobsConfig:
    # Contas em exclusão tentadas por ciclo, das mais antigas para as mais novas.
    accounts_per_cycle: int = 20
    # Itens por lote em cada etapa da purga e em cada retenção (1.8: lotes de 500).
    batch_size: int = 500


class AccountJobs:
    """Purga das contas com `DELETE /me`, retenção da identidade e limpeza das janelas do limite
    de requisições, um lote por etapa e ciclo.

    A purga de cada conta segue a ordem das FKs: `runs` (Runs terminais), `files` (pede a exclusão
    dos objetos e, quando a retenção normal já os removeu do disco, apaga as linhas) e por fim
    `identity` (chaves, sessões, capacidade e a conta). Cada lote é uma transação; uma etapa que
    ainda não terminou adia as seguintes para o próximo ciclo, e a purga interrompida retoma do
    banco. Conta com Run não terminal espera: `runs` não apaga a Run e não termina.
    """

    def __init__(
        self,
        factory: sessionmaker[Session],
        clock: _Clock = lambda: datetime.now(UTC),
        config: AccountJobsConfig = AccountJobsConfig(),  # noqa: B008 - dataclass imutável
    ) -> None:
        self._factory = factory
        self._clock = clock
        self._config = config

    def run_cycle(self) -> dict[str, int]:
        started = time.monotonic()
        now = self._clock()
        batch = self._config.batch_size
        counts = {
            "accounts_purged": self.purge_accounts(),
            "login_states_removed": self._in_transaction(
                lambda s: identity_purge.delete_expired_login_states(s, now, batch)
            ),
            "web_sessions_removed": self._in_transaction(
                lambda s: identity_purge.delete_ended_web_sessions(s, now, batch)
            ),
            "audit_events_removed": self._in_transaction(
                lambda s: identity_purge.delete_old_audit_events(s, now, batch)
            ),
            "accounts_deletion_overdue": self._in_transaction(
                lambda s: identity_purge.count_overdue_deletions(s, now)
            ),
            "request_windows_removed": self._in_transaction(
                lambda s: rate_limit.delete_expired_windows(s, now, batch)
            ),
            # Tamanho da tabela do limite de requisições depois da limpeza (ADR 028).
            "request_windows": self._in_transaction(rate_limit.count_windows),
        }
        if counts["accounts_deletion_overdue"]:
            # Alerta de causa: o prazo de 24 h da exclusão (1.8) está perto de vencer.
            logger.warning(
                "exclusão de conta atrasada",
                extra={
                    "fields": {
                        "alert": "account_deletion_overdue",
                        "accounts": counts["accounts_deletion_overdue"],
                    }
                },
            )
        fields: dict[str, object] = {"main": True, **counts}
        fields["duration_ms"] = round((time.monotonic() - started) * 1000)
        logger.info("ciclo de contas", extra={"fields": fields})
        return counts

    def purge_accounts(self) -> int:
        """Avança a purga das contas em exclusão; devolve quantas terminaram neste ciclo."""
        owners = self._in_transaction(
            lambda s: identity_purge.accounts_pending_purge(s, self._config.accounts_per_cycle)
        )
        return sum(self._purge_account(owner_id) for owner_id in owners)

    def _purge_account(self, owner_id: UUID) -> bool:
        batch = self._config.batch_size
        if not self._in_transaction(lambda s: runs_purge.purge_owner_batch(s, owner_id, batch)):
            return False
        if not self._in_transaction(lambda s: files_purge.purge_owner_batch(s, owner_id, batch)):
            return False
        self._in_transaction(lambda s: identity_purge.purge_owner(s, owner_id))
        logger.info("conta purgada", extra={"fields": {"user_id": str(owner_id)}})
        return True

    def _in_transaction[T](self, work: Callable[[Session], T]) -> T:
        with unit_of_work(self._factory) as session:
            return work(session)


if __name__ == "__main__":
    raise SystemExit(main())
