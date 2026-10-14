"""Ciclo de vida de uma execução: a tabela de transições de docs/GoblinShiftPlan.md, 3.5.

Função pura. Recebe o estado da Run lido do banco e um evento, e devolve o novo estado com
os efeitos que o código com banco precisa aplicar, ou uma recusa com o motivo. Nunca lê o
relógio nem sorteia: `now` e o jitter chegam nos eventos, para que a mesma entrada produza
sempre a mesma decisão e o teste não dependa do tempo real.

A função decide; quem persiste continua obrigado a condicionar cada `UPDATE` à geração e ao
status lidos (fencing, 3.4), porque entre a leitura e a escrita outro processo pode ter agido.
"""

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Self, assert_never

# Tentativas físicas por execução, contando a primeira (3.4, "Retentativa").
MAX_ATTEMPTS = 2
# Claims por execução; no seguinte ela é tratada como mensagem venenosa (3.4).
MAX_CLAIMS = 4
# Prazo para a execução começar, contado da aceitação (1.7, "Fila").
QUEUE_WINDOW = timedelta(hours=2)
# Prazo da execução inteira, contado do início da primeira tentativa (1.7, "Duração").
RUN_DURATION = timedelta(minutes=30)
# Espera antes da retentativa; o sorteio fica com quem chama (3.4).
MIN_RETRY_JITTER = timedelta(seconds=5)
MAX_RETRY_JITTER = timedelta(seconds=30)


class Status(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    STOPPING = "stopping"
    SUCCEEDED = "succeeded"
    SUCCEEDED_WITH_WARNINGS = "succeeded_with_warnings"
    CANCELED = "canceled"
    FAILED = "failed"


TERMINAL_STATUSES = frozenset(
    {Status.SUCCEEDED, Status.SUCCEEDED_WITH_WARNINGS, Status.CANCELED, Status.FAILED}
)


class Phase(StrEnum):
    """Fase dentro de `running`; nos demais estados a fase é `None`."""

    INSPECTING = "inspecting"
    PROCESSING = "processing"
    PACKAGING = "packaging"
    FINALIZING = "finalizing"
    RETRY_WAIT = "retry_wait"


# Ordem do trabalho de uma tentativa. `retry_wait` fica fora: só a falha transitória leva a ela.
WORK_PHASES = (Phase.INSPECTING, Phase.PROCESSING, Phase.PACKAGING, Phase.FINALIZING)


class ErrorCode(StrEnum):
    """Códigos do catálogo (1.6) com que esta máquina termina ou para uma execução."""

    NO_VALID_OUTPUTS = "NO_VALID_OUTPUTS"
    OUTPUT_LIMIT_EXCEEDED = "OUTPUT_LIMIT_EXCEEDED"
    REPORT_LIMIT_EXCEEDED = "REPORT_LIMIT_EXCEEDED"
    RESOURCE_LIMIT_EXCEEDED = "RESOURCE_LIMIT_EXCEEDED"
    EXECUTOR_UNAVAILABLE = "EXECUTOR_UNAVAILABLE"
    QUEUE_EXPIRED = "QUEUE_EXPIRED"
    DEADLINE_EXCEEDED = "DEADLINE_EXCEEDED"
    INFRA_POISON = "INFRA_POISON"
    CANCELED = "CANCELED"


# Falhas que o executor relata ao terminar. NO_VALID_OUTPUTS fica fora porque chega pela
# publicação (tabela de 3.5), e uma falha só pode ter um caminho até o estado terminal.
DETERMINISTIC_CODES = frozenset(
    {
        ErrorCode.OUTPUT_LIMIT_EXCEEDED,
        ErrorCode.REPORT_LIMIT_EXCEEDED,
        ErrorCode.RESOURCE_LIMIT_EXCEEDED,
    }
)


class PublishOutcome(StrEnum):
    SUCCEEDED = "succeeded"
    SUCCEEDED_WITH_WARNINGS = "succeeded_with_warnings"
    NO_VALID_OUTPUTS = "no_valid_outputs"


class InconsistentRunStateError(ValueError):
    """O snapshot combina campos que nenhuma sequência de transições produz."""


@dataclass(frozen=True, kw_only=True)
class RunState:
    """O que a decisão precisa saber da Run. Nasce coerente ou não nasce.

    `stop_reason` é a primeira razão de parada persistida e nunca muda depois de gravada;
    `cancel_requested` registra o pedido mesmo quando outra razão chegou antes.
    `attempts_used` conta tentativas físicas iniciadas; `claim_count`, claims bem-sucedidos.
    """

    status: Status
    phase: Phase | None
    generation: int
    claim_count: int
    attempts_used: int
    eligible_at: datetime
    queue_expires_at: datetime
    deadline_at: datetime | None
    cancel_requested: bool
    stop_reason: ErrorCode | None
    error_code: ErrorCode | None
    has_published_outputs: bool

    def __post_init__(self) -> None:
        for moment in (self.eligible_at, self.queue_expires_at, self.deadline_at):
            if moment is not None:
                _require_timezone(moment)
        problem = _find_inconsistency(self)
        if problem is not None:
            raise InconsistentRunStateError(f"{problem}: {self}")

    @classmethod
    def queued(cls, accepted_at: datetime) -> Self:
        """Run recém-aceita: elegível na hora, com o prazo de fila correndo."""
        return cls(
            status=Status.QUEUED,
            phase=None,
            generation=0,
            claim_count=0,
            attempts_used=0,
            eligible_at=accepted_at,
            queue_expires_at=accepted_at + QUEUE_WINDOW,
            deadline_at=None,
            cancel_requested=False,
            stop_reason=None,
            error_code=None,
            has_published_outputs=False,
        )

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    @property
    def has_live_attempt(self) -> bool:
        """Há uma Attempt sem fim registrado, isto é, um executor possivelmente vivo."""
        if self.status is Status.STOPPING:
            return True
        return self.status is Status.RUNNING and self.phase is not Phase.RETRY_WAIT


# Eventos. Os que vêm de uma tentativa carregam a geração dela, o token de fencing (3.4).


@dataclass(frozen=True)
class Claim:
    now: datetime

    def __post_init__(self) -> None:
        _require_timezone(self.now)


@dataclass(frozen=True)
class CancelRequested:
    pass


@dataclass(frozen=True)
class QueueExpired:
    now: datetime

    def __post_init__(self) -> None:
        _require_timezone(self.now)


@dataclass(frozen=True)
class DeadlineExceeded:
    now: datetime

    def __post_init__(self) -> None:
        _require_timezone(self.now)


@dataclass(frozen=True)
class PhaseReached:
    generation: int
    phase: Phase


@dataclass(frozen=True)
class Published:
    generation: int
    outcome: PublishOutcome
    now: datetime

    def __post_init__(self) -> None:
        _require_timezone(self.now)


@dataclass(frozen=True)
class DeterministicFailure:
    generation: int
    code: ErrorCode

    def __post_init__(self) -> None:
        if self.code not in DETERMINISTIC_CODES:
            raise ValueError(f"{self.code} não é falha determinística do executor")


@dataclass(frozen=True)
class TransientFailure:
    generation: int
    now: datetime
    jitter: timedelta

    def __post_init__(self) -> None:
        _require_timezone(self.now)
        if not MIN_RETRY_JITTER <= self.jitter <= MAX_RETRY_JITTER:
            raise ValueError(
                f"jitter {self.jitter} fora de [{MIN_RETRY_JITTER}, {MAX_RETRY_JITTER}]"
            )


@dataclass(frozen=True)
class ExecutorStopped:
    """O `executor-ctl` confirmou que o executor e seus descendentes terminaram."""

    generation: int


type Event = (
    Claim
    | CancelRequested
    | QueueExpired
    | DeadlineExceeded
    | PhaseReached
    | Published
    | DeterministicFailure
    | TransientFailure
    | ExecutorStopped
)


# Efeitos declarativos, aplicados pelo código com banco na ordem em que aparecem.


@dataclass(frozen=True)
class StartAttempt:
    generation: int
    number: int


@dataclass(frozen=True)
class EndAttempt:
    generation: int


@dataclass(frozen=True)
class RequestStop:
    generation: int


@dataclass(frozen=True)
class PublishOutputs:
    generation: int


@dataclass(frozen=True)
class DiscardCandidates:
    generation: int


@dataclass(frozen=True)
class ReleaseReservations:
    """Libera reservas e usos de objetos; a liberação no banco é idempotente (3.3)."""


type Effect = (
    StartAttempt
    | EndAttempt
    | RequestStop
    | PublishOutputs
    | DiscardCandidates
    | ReleaseReservations
)


@dataclass(frozen=True)
class Transition:
    run: RunState
    effects: tuple[Effect, ...]


class RefusalReason(StrEnum):
    TERMINAL = "terminal"
    INVALID_FOR_STATUS = "invalid_for_status"
    NOT_ELIGIBLE = "not_eligible"
    NOT_YET_DUE = "not_yet_due"
    DEADLINE_PASSED = "deadline_passed"
    STALE_GENERATION = "stale_generation"
    PHASE_NOT_FORWARD = "phase_not_forward"


@dataclass(frozen=True)
class Refused:
    reason: RefusalReason


def decide_transition(run: RunState, event: Event) -> Transition | Refused:
    """Aplica a tabela de 3.5 a um evento; o que ela não prevê é recusado com o motivo."""
    if run.is_terminal:
        return Refused(RefusalReason.TERMINAL)
    match event:
        case Claim():
            return _decide_claim(run, event.now)
        case CancelRequested():
            return _decide_cancel(run)
        case QueueExpired():
            return _decide_queue_expiry(run, event.now)
        case DeadlineExceeded():
            return _decide_deadline(run, event.now)
        case PhaseReached():
            return _decide_phase(run, event)
        case Published():
            return _decide_publication(run, event)
        case DeterministicFailure():
            return _decide_deterministic_failure(run, event)
        case TransientFailure():
            return _decide_transient_failure(run, event)
        case ExecutorStopped():
            return _decide_executor_stopped(run, event.generation)
        case _:
            assert_never(event)


def _decide_claim(run: RunState, now: datetime) -> Transition | Refused:
    if run.status is Status.QUEUED:
        if now >= run.queue_expires_at:
            return Refused(RefusalReason.NOT_ELIGIBLE)
    elif run.phase is not Phase.RETRY_WAIT:
        return Refused(RefusalReason.INVALID_FOR_STATUS)
    if now < run.eligible_at:
        return Refused(RefusalReason.NOT_ELIGIBLE)
    if _deadline_passed(run, now):
        return Refused(RefusalReason.DEADLINE_PASSED)
    if run.claim_count >= MAX_CLAIMS:
        return _end_without_executor(run, ErrorCode.INFRA_POISON)
    generation = run.generation + 1
    number = run.attempts_used + 1
    started = replace(
        run,
        status=Status.RUNNING,
        phase=Phase.INSPECTING,
        generation=generation,
        claim_count=run.claim_count + 1,
        attempts_used=number,
        deadline_at=run.deadline_at or now + RUN_DURATION,
    )
    return Transition(started, (StartAttempt(generation=generation, number=number),))


def _decide_cancel(run: RunState) -> Transition:
    if run.status is Status.STOPPING:
        # A parada já foi pedida; o cancelamento só fica registrado, e a primeira razão vence.
        return Transition(replace(run, cancel_requested=True), ())
    if run.has_live_attempt:
        stop = _request_stop(run, ErrorCode.CANCELED)
    else:
        stop = _end_without_executor(run, ErrorCode.CANCELED)
    return replace(stop, run=replace(stop.run, cancel_requested=True))


def _decide_queue_expiry(run: RunState, now: datetime) -> Transition | Refused:
    if run.status is not Status.QUEUED:
        return Refused(RefusalReason.INVALID_FOR_STATUS)
    if now <= run.queue_expires_at:
        return Refused(RefusalReason.NOT_YET_DUE)
    return _end_without_executor(run, ErrorCode.QUEUE_EXPIRED)


def _decide_deadline(run: RunState, now: datetime) -> Transition | Refused:
    if run.status is Status.QUEUED:
        # O prazo de execução só começa a correr no primeiro claim.
        return Refused(RefusalReason.INVALID_FOR_STATUS)
    if not _deadline_passed(run, now):
        return Refused(RefusalReason.NOT_YET_DUE)
    if run.status is Status.STOPPING:
        return Transition(run, ())
    if run.has_live_attempt:
        return _request_stop(run, ErrorCode.DEADLINE_EXCEEDED)
    return _end_without_executor(run, ErrorCode.DEADLINE_EXCEEDED)


def _decide_phase(run: RunState, event: PhaseReached) -> Transition | Refused:
    refusal = _refuse_unless_working(run, event.generation)
    if refusal is not None:
        return refusal
    if not _moves_forward(run.phase, event.phase):
        return Refused(RefusalReason.PHASE_NOT_FORWARD)
    return Transition(replace(run, phase=event.phase), ())


def _decide_publication(run: RunState, event: Published) -> Transition | Refused:
    refusal = _refuse_unless_working(run, event.generation)
    if refusal is not None:
        return refusal
    # O prazo vencido é razão de parada; publicar depois dele furaria a primeira razão.
    if _deadline_passed(run, event.now):
        return Refused(RefusalReason.DEADLINE_PASSED)
    status, error_code = _PUBLISHED_AS[event.outcome]
    published = replace(
        run,
        status=status,
        phase=None,
        error_code=error_code,
        has_published_outputs=True,
    )
    effects = (PublishOutputs(run.generation), EndAttempt(run.generation), ReleaseReservations())
    return Transition(published, effects)


_PUBLISHED_AS: dict[PublishOutcome, tuple[Status, ErrorCode | None]] = {
    PublishOutcome.SUCCEEDED: (Status.SUCCEEDED, None),
    PublishOutcome.SUCCEEDED_WITH_WARNINGS: (Status.SUCCEEDED_WITH_WARNINGS, None),
    PublishOutcome.NO_VALID_OUTPUTS: (Status.FAILED, ErrorCode.NO_VALID_OUTPUTS),
}


def _decide_deterministic_failure(
    run: RunState, event: DeterministicFailure
) -> Transition | Refused:
    refusal = _refuse_unless_working(run, event.generation)
    if refusal is not None:
        return refusal
    failed = replace(run, status=Status.FAILED, phase=None, error_code=event.code)
    return Transition(failed, _executor_finished_effects(run.generation))


def _decide_transient_failure(run: RunState, event: TransientFailure) -> Transition | Refused:
    refusal = _refuse_unless_working(run, event.generation)
    if refusal is not None:
        return refusal
    if _deadline_passed(run, event.now):
        return _request_stop(run, ErrorCode.DEADLINE_EXCEEDED)
    if run.attempts_used >= MAX_ATTEMPTS:
        return _request_stop(run, ErrorCode.EXECUTOR_UNAVAILABLE)
    waiting = replace(run, phase=Phase.RETRY_WAIT, eligible_at=event.now + event.jitter)
    return Transition(waiting, (EndAttempt(run.generation),))


def _decide_executor_stopped(run: RunState, generation: int) -> Transition | Refused:
    if generation != run.generation:
        return Refused(RefusalReason.STALE_GENERATION)
    if run.status is not Status.STOPPING:
        return Refused(RefusalReason.INVALID_FOR_STATUS)
    reason = run.stop_reason
    if reason is None:
        raise InconsistentRunStateError(f"stopping sem razão de parada: {run}")
    status = Status.CANCELED if reason is ErrorCode.CANCELED else Status.FAILED
    stopped = replace(run, status=status, error_code=reason)
    return Transition(stopped, _executor_finished_effects(run.generation))


def _refuse_unless_working(run: RunState, generation: int) -> Refused | None:
    """Só a tentativa viva da geração atual escreve; um worker atrasado é recusado."""
    if generation != run.generation:
        return Refused(RefusalReason.STALE_GENERATION)
    if run.status is not Status.RUNNING or not run.has_live_attempt:
        return Refused(RefusalReason.INVALID_FOR_STATUS)
    return None


def _request_stop(run: RunState, reason: ErrorCode) -> Transition:
    """Executor possivelmente vivo: grava a razão e pede a parada antes de concluir."""
    stopping = replace(run, status=Status.STOPPING, phase=None, stop_reason=reason)
    return Transition(stopping, (RequestStop(run.generation),))


def _end_without_executor(run: RunState, reason: ErrorCode) -> Transition:
    """Nenhum executor vivo: a razão de parada já decide o estado terminal."""
    status = Status.CANCELED if reason is ErrorCode.CANCELED else Status.FAILED
    ended = replace(run, status=status, phase=None, stop_reason=reason, error_code=reason)
    return Transition(ended, (ReleaseReservations(),))


def _executor_finished_effects(generation: int) -> tuple[Effect, ...]:
    return (EndAttempt(generation), DiscardCandidates(generation), ReleaseReservations())


def _deadline_passed(run: RunState, now: datetime) -> bool:
    return run.deadline_at is not None and now > run.deadline_at


def _moves_forward(current: Phase | None, target: Phase) -> bool:
    if current not in WORK_PHASES or target not in WORK_PHASES:
        return False
    return WORK_PHASES.index(target) > WORK_PHASES.index(current)


def _require_timezone(moment: datetime) -> None:
    # Instantes sem fuso não se comparam com os do banco (`timestamptz`), que são UTC.
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError(f"instante sem fuso: {moment}")


def _find_inconsistency(run: RunState) -> str | None:
    return (
        _find_phase_inconsistency(run)
        or _find_counter_inconsistency(run)
        or _find_outcome_inconsistency(run)
    )


def _find_phase_inconsistency(run: RunState) -> str | None:
    if (run.phase is not None) != (run.status is Status.RUNNING):
        return "fase só existe em running"
    if run.phase is Phase.RETRY_WAIT and run.attempts_used >= MAX_ATTEMPTS:
        return "retry_wait sem tentativa restante"
    return None


def _find_counter_inconsistency(run: RunState) -> str | None:
    if not 0 <= run.attempts_used <= min(run.claim_count, MAX_ATTEMPTS):
        return "tentativas fora de [0, min(claims, MAX_ATTEMPTS)]"
    if run.claim_count > MAX_CLAIMS:
        return "claims acima do teto"
    if (run.attempts_used == 0) != (run.deadline_at is None):
        return "o prazo existe exatamente quando alguma tentativa começou"
    if run.status is Status.QUEUED and run.attempts_used != 0:
        return "execução na fila com tentativa"
    return None


def _find_outcome_inconsistency(run: RunState) -> str | None:
    stopped_statuses = {Status.STOPPING, Status.CANCELED, Status.FAILED}
    if run.stop_reason is not None and run.status not in stopped_statuses:
        return "razão de parada fora de stopping, canceled ou failed"
    if run.status is Status.STOPPING and run.stop_reason is None:
        return "stopping sem razão de parada"
    if run.cancel_requested and run.status not in stopped_statuses:
        return "cancelamento pedido sem parada"
    if (run.error_code is not None) != (run.status in {Status.CANCELED, Status.FAILED}):
        return "código de erro existe exatamente em canceled e failed"
    if run.status is Status.CANCELED and run.error_code is not ErrorCode.CANCELED:
        return "canceled sem o código CANCELED"
    if run.has_published_outputs and run.status not in _PUBLISHED_STATUSES:
        return "saídas publicadas fora de um término por publicação"
    return None


_PUBLISHED_STATUSES = frozenset({Status.SUCCEEDED, Status.SUCCEEDED_WITH_WARNINGS, Status.FAILED})
