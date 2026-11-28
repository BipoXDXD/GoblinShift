"""Processo `gs-maintenance`: reconciliação e retenção em ciclos (docs/GoblinShiftPlan.md, 3.4).

Um ciclo a cada `GS_MAINTENANCE_INTERVAL_S` (30 s): o de execuções e arquivos (`Maintenance`), o
de contas (`AccountJobs`: purga das contas em exclusão, retenção da identidade, 1.8, e limpeza das
janelas do limite de requisições, ADR 028) e, com o `executor_ctl`, a sonda da imagem do executor
(`ExecutorImageProbe`). Cada volta é um span `maintenance.cycle` com `main = true` (3.7), com as
contagens dos ciclos, que os alertas de causa de `deploy/openobserve` leem; cada ciclo também
registra seu evento de log. A falha de um ciclo é registrada e o seguinte tenta de novo. Os lotes
usam
`statement_timeout` de `GS_MAINTENANCE_STATEMENT_TIMEOUT_MS` (30 s, 3.2).

SIGTERM e SIGINT terminam o ciclo em andamento e saem. O handler só marca uma flag
(`ShutdownFlag`, entrypoints/shutdown.py), sem trava nem log, e a espera entre as voltas dorme em
fatias curtas para vê-la.
"""

import logging
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum, auto
from functools import partial
from uuid import UUID

from opentelemetry.trace import Status, StatusCode, Tracer
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.entrypoints.bootstrap import build_runner, process_tracer
from goblinshift.entrypoints.shutdown import ShutdownFlag, shutdown_on_signals
from goblinshift.files import purge as files_purge
from goblinshift.files.storage import FileStorage
from goblinshift.identity import purge as identity_purge
from goblinshift.identity import rate_limit
from goblinshift.platform.db import (
    is_lock_contention,
    make_engine,
    make_session_factory,
    unit_of_work,
)
from goblinshift.platform.settings import (
    EXIT_CONFIGURATION,
    ExecutorConfigError,
    MissingSecretError,
    Settings,
    load_settings_or_exit,
)
from goblinshift.platform.telemetry import UserHasher, configure_logging, exception_fields
from goblinshift.runs import purge as runs_purge
from goblinshift.runs.finished import RunFinishedReporter
from goblinshift.runs.maintenance import (
    STEPS_FAILED,
    Maintenance,
    MaintenanceConfig,
    is_connection_error,
    log_step_failure,
)
from goblinshift.runs.worker.executor_ctl_runner import ExecutorCtlError, ExecutorCtlRunner
from goblinshift.runs.worker.runner import Runner

logger = logging.getLogger("goblinshift.maintenance")

EXIT_OK = 0
EXIT_FAILURE = 1

type _Clock = Callable[[], datetime]
# Cancela a Run não terminal da conta (`runs.purge.cancel_active_run` com fábrica e storage).
type ActiveRunCanceller = Callable[[UUID], object]
type Cycle = tuple[str, Callable[[], Mapping[str, int]]]

CYCLE_SPAN = "maintenance.cycle"
_ATTRIBUTE_PREFIX = "maintenance."
# Contagens do ciclo de contas. Nome próprio, e não `steps_failed`: o span junta as contagens de
# todos os ciclos, e o ciclo de contas sobrescreveria a do ciclo de execuções.
ACCOUNT_STEPS_FAILED = "account_steps_failed"
ACCOUNTS_PURGE_FAILED = "accounts_purge_failed"
ACCOUNTS_CANCEL_FAILED = "accounts_cancel_failed"
# Contagens de passo falho que fazem a volta contar em `failed_cycles`.
_FAILED_STEP_COUNTS = (STEPS_FAILED, ACCOUNT_STEPS_FAILED)
# Prazo do laço da retenção da auditoria em cada ciclo de contas (#442, 50b): os lotes repetem
# até a fila de vencidos acabar ou o prazo passar, e o resto fica para o ciclo seguinte.
AUDIT_RETENTION_TIME_BUDGET = timedelta(seconds=20)


def main() -> int:
    configure_logging()
    settings = load_settings_or_exit(logger)
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
        storage = FileStorage(settings.storage_root)
        try:
            # Um provider por processo: o `maintenance.cycle` e o `run.finished` saem pelo mesmo
            # lote, e a parada espera um prazo de encerramento só.
            finished = RunFinishedReporter(factory, UserHasher(settings.user_hash_key()), tracer)
            maintenance = Maintenance(
                factory,
                storage,
                runner,
                MaintenanceConfig(missing_max_absent_ratio=settings.missing_sweep_max_absent_ratio),
                on_finished=finished.report,
            )
            account_jobs = AccountJobs(
                factory,
                cancel_active_run=partial(
                    runs_purge.cancel_active_run, factory, storage, on_finished=finished.report
                ),
            )
            logger.info("manutenção pronta")
            cycles: tuple[Cycle, ...] = (
                # A parada é conferida entre os passos, e a volta inteira não caberia na carência
                # do Docker. Ociosa ou com o banco fora (o passo desiste no `connect_timeout`), a
                # parada mais o prazo do tracing cabe (tests/runs/test_process_tracer_shutdown.py).
                # Com o banco lento, não: um statement pode levar o `statement_timeout` da
                # manutenção, e o SIGKILL pode chegar antes do fim dele. Risco aceito: perde os
                # spans pendentes, não dados, porque o statement roda numa transação aberta, que
                # o PostgreSQL reverte quando a conexão cai, e o passo se repete no ciclo seguinte.
                ("execuções", partial(maintenance.run_cycle, should_stop=shutdown.is_set)),
                ("contas", account_jobs.run_cycle),
                *executor_image_cycles(settings, runner),
            )
            while not shutdown.is_set():
                run_cycles(tracer, cycles, should_stop=shutdown.is_set)
                shutdown.wait(settings.maintenance_interval_s)
            logger.info("parada pedida", extra={"fields": {"signal": shutdown.signum}})
        finally:
            engine.dispose()
    logger.info("manutenção encerrada")


def executor_image_cycles(settings: Settings, runner: Runner) -> tuple[Cycle, ...]:
    """A sonda da imagem, só com o `executor_ctl`: o Runner local não usa daemon nem imagem."""
    if not isinstance(runner, ExecutorCtlRunner):
        return ()
    probe = ExecutorImageProbe(runner.image_present, settings.executor_image_for_runs())
    return (("imagem do executor", probe.run_cycle),)


def run_cycles(
    tracer: Tracer, cycles: Sequence[Cycle], should_stop: Callable[[], bool] = lambda: False
) -> None:
    """Uma volta: roda os ciclos em sequência dentro de um span `maintenance.cycle`.

    Com a parada pedida (`should_stop`), o ciclo seguinte não começa; o span sai com o que rodou.

    Ciclos independentes: a falha de um não impede o outro. O span leva as contagens de todos,
    com o prefixo `maintenance.`, e quantos falharam: os que levantaram e os que contaram passos
    falhos (`steps_failed`, `account_steps_failed`); a mensagem da exceção fica só no log, porque
    pode trazer valores do banco (3.7).
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
            if any(counts.get(key, 0) > 0 for key in _FAILED_STEP_COUNTS):
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

    def __post_init__(self) -> None:
        # Zero não anda a fila: a purga e as retenções nunca terminariam.
        for field in ("accounts_per_cycle", "batch_size"):
            if getattr(self, field) < 1:
                raise ValueError(f"{field} precisa ser pelo menos 1")


class _PartialStepError(Exception):
    """Passo que falhou depois de confirmar parte do trabalho; leva as contagens do parcial."""

    def __init__(self, counts: dict[str, int], cause: Exception) -> None:
        super().__init__(type(cause).__name__)
        self.counts = counts
        self.cause = cause


class AccountJobs:
    """Purga das contas com `DELETE /me`, retenção da identidade e limpeza das janelas do limite
    de requisições, um lote por etapa e ciclo.

    A retenção da auditoria é a exceção ao lote por ciclo: repete lotes, cada um na sua transação,
    até `AUDIT_RETENTION_TIME_BUDGET`, porque a entrada pode passar de um lote a cada 30 s. Evento
    com mais de 91 dias conta em `audit_events_overdue` e gera o alerta `audit_retention_behind`.

    A purga de cada conta segue a ordem das FKs: `runs` (Runs terminais), `files` (pede a exclusão
    dos objetos e, quando a retenção normal já os removeu do disco, apaga as linhas) e por fim
    `identity` (chaves, sessões, capacidade e a conta). Cada lote é uma transação; uma etapa que
    ainda não terminou adia as seguintes para o próximo ciclo, e a purga interrompida retoma do
    banco. Conta com Run não terminal espera: `runs` não apaga a Run e não termina, e a purga pede
    o cancelamento dela (`cancel_active_run`), que o `DELETE /me` pode não ter completado (#442).

    Passos independentes, como no ciclo de execuções: a exceção de um fica no log e em
    `account_steps_failed`, e os outros seguem, inclusive o alerta da exclusão atrasada; as
    contagens do passo que falhou ficam fora do evento. Na purga, a falha de uma conta conta em
    `accounts_purge_failed` e não impede as seguintes: cada ciclo marca a tentativa nas contas
    que pega, e a fila segue pela tentativa mais antiga (#442). O cancelamento que falha conta em
    `accounts_cancel_failed`, e a conta volta num ciclo seguinte, quando chegar a vez dela na
    fila. Só a disputa com o worker segurando a Run (`lock_timeout` ou `statement_timeout`) fica
    fora de `account_steps_failed`; qualquer outra falha (permissão, bug, storage) põe a volta no
    alerta, como a purga que falha.
    Perda de conexão derruba o ciclo inteiro, como antes: com o banco fora, cada passo seguinte só
    esperaria o `connect_timeout` de novo.
    """

    def __init__(
        self,
        factory: sessionmaker[Session],
        *,
        cancel_active_run: ActiveRunCanceller,
        clock: _Clock = lambda: datetime.now(UTC),
        config: AccountJobsConfig = AccountJobsConfig(),  # noqa: B008 - dataclass imutável
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._factory = factory
        self._cancel_active_run = cancel_active_run
        self._clock = clock
        self._config = config
        # Mede a duração do ciclo e o prazo da retenção da auditoria; os testes o trocam por um
        # relógio falso.
        self._monotonic = monotonic

    def run_cycle(self) -> dict[str, int]:
        started = self._monotonic()
        counts: dict[str, int] = {}
        failed = 0
        for name, step in self._cycle_steps():
            try:
                counts.update(step())
            except _PartialStepError as partial:
                # Os lotes já confirmados entram no evento; a falha conta como a de qualquer passo.
                counts.update(partial.counts)
                log_step_failure(name, partial.cause)
                failed += 1
            except Exception as error:
                if is_connection_error(error):
                    raise
                # Fronteira do passo: o seguinte recomeça do banco, sem estado em memória.
                log_step_failure(name, error)
                failed += 1
        # A purga diz se ela mesma conta como passo falho (`purge_accounts`), mesmo com as outras
        # contas purgadas; se levantou exceção, já está em `failed`.
        counts[ACCOUNT_STEPS_FAILED] = failed + counts.get(ACCOUNT_STEPS_FAILED, 0)
        overdue = counts.get("accounts_deletion_overdue", 0)
        if overdue:
            # Alerta de causa: o prazo de 24 h da exclusão (1.8) está perto de vencer.
            logger.warning(
                "exclusão de conta atrasada",
                extra={"fields": {"alert": "account_deletion_overdue", "accounts": overdue}},
            )
        audit_overdue = counts.get("audit_events_overdue", 0)
        if audit_overdue:
            # Alerta de causa: a retenção de 90 dias da auditoria (1.7) atrasou mais de um dia.
            logger.warning(
                "retenção da auditoria atrasada",
                extra={"fields": {"alert": "audit_retention_behind", "events": audit_overdue}},
            )
        fields: dict[str, object] = {"main": True, **counts}
        fields["duration_ms"] = round((self._monotonic() - started) * 1000)
        logger.info("ciclo de contas", extra={"fields": fields})
        return counts

    def _cycle_steps(self) -> tuple[Cycle, ...]:
        """Os passos, na ordem: purga, retenção da identidade, alertas e limite de requisições.

        Cada passo tem o nome da contagem que devolve, e é esse nome que vai ao log da falha.
        """
        now = self._clock()
        batch = self._config.batch_size
        counted = self._counted_step
        return (
            ("accounts_purged", self.purge_accounts),
            counted(
                "login_states_removed",
                lambda s: identity_purge.delete_expired_login_states(s, now, batch),
            ),
            counted(
                "web_sessions_removed",
                lambda s: identity_purge.delete_ended_web_sessions(s, now, batch),
            ),
            counted(
                "api_keys_removed",
                lambda s: identity_purge.delete_inactive_api_keys(s, now, batch),
            ),
            # Laço de lotes, cada um na sua transação: não cabe em `_counted_step`.
            (
                "audit_events_removed",
                lambda: {"audit_events_removed": self._remove_old_audit_events(now, batch)},
            ),
            counted(
                "audit_events_overdue",
                lambda s: identity_purge.count_overdue_audit_events(s, now),
            ),
            # Tamanho da auditoria pelas estatísticas do banco, sem varrer a tabela.
            counted("audit_events_estimated", identity_purge.estimate_audit_events),
            counted(
                "accounts_deletion_overdue",
                lambda s: identity_purge.count_overdue_deletions(s, now),
            ),
            counted(
                "request_windows_removed",
                lambda s: rate_limit.delete_expired_windows(s, now, batch),
            ),
            # Tamanho da tabela do limite de requisições depois da limpeza (ADR 028).
            counted("request_windows", rate_limit.count_windows),
        )

    def _counted_step(self, key: str, work: Callable[[Session], int]) -> Cycle:
        return key, lambda: {key: self._in_transaction(work)}

    def _remove_old_audit_events(self, now: datetime, batch: int) -> int:
        """Lotes da retenção da auditoria até um vir incompleto ou o prazo passar; a soma.

        O prazo é conferido depois de cada lote, então o ciclo sempre apaga pelo menos um. Quando
        ele passa, o laço para sem exceção e conta o parcial; o ciclo seguinte continua do banco.
        Um lote que falha depois de outros confirmados sobe em `_PartialStepError`, com a soma
        até ali; perda de conexão e falha no primeiro lote sobem como estão.
        """
        deadline = self._monotonic() + AUDIT_RETENTION_TIME_BUDGET.total_seconds()
        removed = 0
        while True:
            try:
                deleted = self._in_transaction(
                    lambda s: identity_purge.delete_old_audit_events(s, now, batch)
                )
            except Exception as error:
                if removed == 0 or is_connection_error(error):
                    raise
                raise _PartialStepError({"audit_events_removed": removed}, error) from error
            removed += deleted
            if deleted < batch or self._monotonic() >= deadline:
                return removed

    def purge_accounts(self) -> dict[str, int]:
        """Avança a purga das contas em exclusão: quantas terminaram e quantas falharam no ciclo.

        `account_steps_failed` é `1` quando a purga põe a volta no alerta: falhou em alguma conta
        ou num cancelamento que não foi disputa com o worker. Desistir da trava (55P03) ou do
        `statement_timeout` (57014) com o worker segurando a Run é esperado e se resolve num ciclo
        seguinte; fica só em `accounts_cancel_failed`.
        """
        owners = self._in_transaction(
            lambda s: identity_purge.claim_accounts_for_purge(s, self._config.accounts_per_cycle)
        )
        purged = 0
        failed = 0
        cancel_failed = 0
        cancel_broken = 0
        for owner_id in owners:
            try:
                outcome = self._purge_account(owner_id)
            except Exception as error:
                if is_connection_error(error):
                    raise
                # Uma conta com defeito vai para o fim da fila; as seguintes não esperam por ela.
                _log_purge_failure(owner_id, error)
                failed += 1
                continue
            if outcome is _Purge.DONE:
                purged += 1
            elif outcome is _Purge.CANCEL_CONTENDED:
                cancel_failed += 1
            elif outcome is _Purge.CANCEL_FAILED:
                cancel_failed += 1
                cancel_broken += 1
        return {
            "accounts_purged": purged,
            ACCOUNTS_PURGE_FAILED: failed,
            ACCOUNTS_CANCEL_FAILED: cancel_failed,
            ACCOUNT_STEPS_FAILED: int(failed > 0 or cancel_broken > 0),
        }

    def _purge_account(self, owner_id: UUID) -> _Purge:
        batch = self._config.batch_size
        if not self._in_transaction(lambda s: runs_purge.purge_owner_batch(s, owner_id, batch)):
            return self._cancel_pending_run(owner_id)
        if not self._in_transaction(lambda s: files_purge.purge_owner_batch(s, owner_id, batch)):
            return _Purge.PENDING
        self._in_transaction(lambda s: identity_purge.purge_owner(s, owner_id))
        logger.info("conta purgada", extra={"fields": {"user_id": str(owner_id)}})
        return _Purge.DONE

    def _cancel_pending_run(self, owner_id: UUID) -> _Purge:
        """Cancela a Run não terminal que o `DELETE /me` deixou (falha depois do commit, #442).

        Repetir a cada ciclo é seguro: o cancelamento é idempotente e, sem Run não terminal, não
        faz nada. A purga segue quando a Run terminar. A falha fica no log e na contagem, e a conta
        volta num ciclo seguinte, quando chegar a vez dela na fila.
        """
        try:
            self._cancel_active_run(owner_id)
        except Exception as error:
            if is_connection_error(error):
                raise
            _log_purge_failure(owner_id, error, message="purga: cancelamento falhou")
            return _Purge.CANCEL_CONTENDED if is_lock_contention(error) else _Purge.CANCEL_FAILED
        return _Purge.PENDING

    def _in_transaction[T](self, work: Callable[[Session], T]) -> T:
        with unit_of_work(self._factory) as session:
            return work(session)


class _Purge(Enum):
    """Como terminou a tentativa de purga de uma conta no ciclo."""

    DONE = auto()
    PENDING = auto()
    # O cancelamento desistiu da Run que o worker segura (55P03 ou 57014): fora do alerta.
    CANCEL_CONTENDED = auto()
    # O cancelamento falhou por outro motivo (permissão, bug, storage): põe a volta no alerta.
    CANCEL_FAILED = auto()


def _log_purge_failure(
    owner_id: UUID, error: Exception, *, message: str = "purga da conta falhou"
) -> None:
    """Como `log_step_failure`: tipo e SQLSTATE nos campos, sem a mensagem do banco (3.7)."""
    driver_error = getattr(error, "orig", error)
    fields = {
        "user_id": str(owner_id),
        "error_type": type(error).__name__,
        "sqlstate": getattr(driver_error, "sqlstate", None),
    }
    logger.exception(message, extra={"fields": fields})


class ExecutorImageProbe:
    """Confere a cada volta se o daemon ainda tem a imagem do executor deste deploy.

    Sem a imagem, o `docker run --pull=never` falha e isso só apareceria quando uma Run tentasse
    iniciar. Ciclo próprio e sem banco: a resposta não depende dos outros. Não lança: uma exceção
    cairia em `causa-ciclos-falhando`, com o diagnóstico errado para Docker ou `executor-ctl` fora.
    """

    def __init__(self, is_present: Callable[[str], bool], image: str) -> None:
        self._is_present = is_present
        self._image = image

    def run_cycle(self) -> dict[str, int]:
        try:
            present = self._is_present(self._image)
        except ExecutorCtlError as error:
            # Docker ou serviço fora, ou recusa: o código diz qual, sem um traceback a cada 30 s.
            logger.warning(
                _PROBE_FAILED_MESSAGE,
                extra={"fields": {**_PROBE_FAILED_EVENT, **exception_fields(error)}},
            )
            return _image_counts(probe_failed=1)
        except Exception:
            # Defeito da própria sonda: mesma contagem, e o traceback para achar a causa.
            logger.exception(_PROBE_FAILED_MESSAGE, extra={"fields": _PROBE_FAILED_EVENT})
            return _image_counts(probe_failed=1)
        if not present:
            logger.error(
                "imagem do executor ausente do daemon",
                extra={"fields": {"alert": "executor_image_missing", "image": self._image}},
            )
        return _image_counts(missing=0 if present else 1)


_PROBE_FAILED_MESSAGE = "sonda da imagem do executor falhou"
_PROBE_FAILED_EVENT = {"alert": "executor_image_probe_failed"}


def _image_counts(*, missing: int = 0, probe_failed: int = 0) -> dict[str, int]:
    return {"executor_image_missing": missing, "executor_image_probe_failed": probe_failed}


if __name__ == "__main__":
    raise SystemExit(main())
