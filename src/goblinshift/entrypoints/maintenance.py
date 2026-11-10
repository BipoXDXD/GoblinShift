"""Processo `gs-maintenance`: reconciliação e retenção em ciclos (docs/GoblinShiftPlan.md, 3.4).

Um ciclo a cada `GS_MAINTENANCE_INTERVAL_S` (30 s): o de execuções e arquivos (`Maintenance`) e o
de contas (`AccountJobs`: purga das contas em exclusão, retenção da identidade, 1.8, e limpeza das
janelas do limite de requisições, ADR 028). Cada volta é um span `maintenance.cycle` com
`main = true` (3.7), com as contagens dos dois ciclos, que os alertas de causa de
`deploy/openobserve` leem; cada ciclo também registra seu evento de log. A falha de um ciclo é
registrada e o seguinte tenta de novo. Os lotes usam
`statement_timeout` de `GS_MAINTENANCE_STATEMENT_TIMEOUT_MS` (30 s, 3.2).

SIGTERM e SIGINT terminam o ciclo em andamento e saem. O handler só marca uma flag
(`ShutdownFlag`, entrypoints/shutdown.py), sem trava nem log, e a espera entre as voltas dorme em
fatias curtas para vê-la.
"""

import logging
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from uuid import UUID

from opentelemetry.trace import Status, StatusCode, Tracer
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.entrypoints.shutdown import ShutdownFlag, shutdown_on_signals
from goblinshift.entrypoints.worker import build_runner, process_tracer
from goblinshift.files import purge as files_purge
from goblinshift.files.storage import FileStorage
from goblinshift.identity import purge as identity_purge
from goblinshift.identity import rate_limit
from goblinshift.platform.db import make_engine, make_session_factory, unit_of_work
from goblinshift.platform.settings import ExecutorConfigError, MissingSecretError, Settings
from goblinshift.platform.telemetry import UserHasher, configure_logging
from goblinshift.runs import purge as runs_purge
from goblinshift.runs.finished import RunFinishedReporter
from goblinshift.runs.maintenance import STEPS_FAILED, Maintenance, MaintenanceConfig
from goblinshift.runs.worker.runner import Runner

logger = logging.getLogger("goblinshift.maintenance")

EXIT_OK = 0
EXIT_FAILURE = 1
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
        settings.database_password()
    except (ExecutorConfigError, MissingSecretError) as error:
        logger.error("manutenção não sobe: %s", error)
        return EXIT_CONFIGURATION
    try:
        serve(settings, runner, shutdown_on_signals())
    except Exception:
        # Ver `worker.main`: a mensagem crua do banco não pode ir para o stderr.
        logger.exception("manutenção encerrada por erro")
        return EXIT_FAILURE
    return EXIT_OK


def serve(settings: Settings, runner: Runner, shutdown: ShutdownFlag) -> None:
    batch_settings = settings.model_copy(
        update={"db_statement_timeout_ms": settings.maintenance_statement_timeout_ms}
    )
    # O tracing fecha por último, depois do pool, e mesmo se o `dispose` falhar: o encerramento
    # descarta span emitido depois dele, e sem ele sobraria o `atexit` do SDK, que não tem prazo.
    with process_tracer(settings, "goblinshift-maintenance") as tracer:
        engine = make_engine(batch_settings)
        factory = make_session_factory(engine)
        try:
            # Um provider por processo: o `maintenance.cycle` e o `run.finished` saem pelo mesmo
            # lote, e a parada espera um prazo de encerramento só.
            finished = RunFinishedReporter(factory, UserHasher(settings.user_hash_key()), tracer)
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
                # A parada é conferida entre os passos: com o banco fora, cada passo pode levar
                # o `connect_timeout`. Esse passo, mais o prazo do tracing, cabe na carência do
                # Docker (tests/runs/test_process_tracer_shutdown.py); a volta inteira não
                # caberia.
                ("execuções", partial(maintenance.run_cycle, should_stop=shutdown.is_set)),
                ("contas", account_jobs.run_cycle),
            )
            while not shutdown.is_set():
                run_cycles(tracer, cycles, should_stop=shutdown.is_set)
                shutdown.wait(settings.maintenance_interval_s)
            logger.info("parada pedida", extra={"fields": {"signal": shutdown.signum}})
        finally:
            engine.dispose()
    logger.info("manutenção encerrada")


def run_cycles(
    tracer: Tracer, cycles: Sequence[Cycle], should_stop: Callable[[], bool] = lambda: False
) -> None:
    """Uma volta: roda os ciclos em sequência dentro de um span `maintenance.cycle`.

    Com a parada pedida (`should_stop`), o ciclo seguinte não começa; o span sai com o que rodou.

    Ciclos independentes: a falha de um não impede o outro. O span leva as contagens de todos,
    com o prefixo `maintenance.`, e quantos falharam: os que levantaram e os que contaram passos
    falhos (`steps_failed`); a mensagem da exceção fica só no log, porque pode trazer valores do
    banco (3.7).
    """
    started = time.monotonic()
    with tracer.start_as_current_span(
        CYCLE_SPAN, record_exception=False, set_status_on_exception=False
    ) as span:
        failed = 0
        for name, cycle in cycles:
            if should_stop():
                break
            try:
                counts = cycle()
            except Exception:
                # Fronteira do processo: o ciclo seguinte recomeça do banco, sem estado em memória.
                logger.exception("ciclo de manutenção falhou", extra={"fields": {"cycle": name}})
                failed += 1
                continue
            for key, value in counts.items():
                span.set_attribute(f"{_ATTRIBUTE_PREFIX}{key}", value)
            if counts.get(STEPS_FAILED, 0) > 0:
                # Passo que falhou dentro do ciclo: as contagens dos outros seguem no span, e a
                # volta entra no mesmo alerta de quem levanta.
                failed += 1
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
