"""Invariantes de 3.5 sob sequências arbitrárias de eventos (docs/GoblinShiftPlan.md, 3.7).

O modelo conta as tentativas sem consultar a lógica testada: uma Attempt nasce com
`StartAttempt` e morre com `EndAttempt`, e vira física quando um início de executor da
geração dela é aceito (`ExecutorStarted` ou `LeaseExpired` com `executor_ran`).
"""

from datetime import UTC, datetime, timedelta

from hypothesis import settings, target
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule

from goblinshift.runs.lifecycle import (
    MAX_ATTEMPTS,
    MAX_CLAIMS,
    CancelRequested,
    Claim,
    DeadlineExceeded,
    DeterministicFailure,
    EndAttempt,
    ErrorCode,
    Event,
    ExecutorStarted,
    ExecutorStopped,
    LeaseExpired,
    Phase,
    PhaseReached,
    Published,
    PublishOutcome,
    PublishOutputs,
    QueueExpired,
    RefusalReason,
    Refused,
    ReleaseReservations,
    RunState,
    StartAttempt,
    Status,
    TransientFailure,
    decide_transition,
)

ACCEPTED_AT = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)

DETERMINISTIC = [
    ErrorCode.OUTPUT_LIMIT_EXCEEDED,
    ErrorCode.REPORT_LIMIT_EXCEEDED,
    ErrorCode.RESOURCE_LIMIT_EXCEEDED,
]

# Passos curtos (até um minuto), três vezes mais prováveis, cabem no jitter e deixam
# retentativas e claims seguidos acontecerem dentro do prazo; os de até uma hora alcançam
# o prazo da fila (2 h) e o da execução (30 min) em poucos passos.
short_step = st.integers(min_value=0, max_value=60)
clock_steps = st.one_of(
    short_step, short_step, short_step, st.integers(min_value=0, max_value=3600)
).map(lambda s: timedelta(seconds=s))
jitters = st.integers(min_value=5, max_value=30).map(lambda s: timedelta(seconds=s))
# Deslocamento de geração: 0 é o worker atual; os demais imitam um worker atrasado.
generation_offsets = st.sampled_from([0, 0, 0, -1, 1])
# Cancelamento e términos encerram a sequência; acontecendo em um de cada quatro sorteios,
# sobram passos para alcançar retentativa, prazo e parada.
rarely = st.integers(min_value=0, max_value=3).map(lambda n: n == 0)


class RunLifecycle(RuleBasedStateMachine):
    def __init__(self) -> None:
        super().__init__()
        self.now = ACCEPTED_AT
        self.run = RunState.queued(ACCEPTED_AT)
        self.started_attempts = 0
        self.ended_attempts = 0
        self.releases = 0
        self.published = False
        self.first_stop_reason: ErrorCode | None = None
        self.transitions = 0
        self.physical_generations: set[int] = set()

    def decide(self, event: Event) -> None:
        before = self.run
        result = decide_transition(before, event)
        if isinstance(result, Refused):
            if before.is_terminal:
                assert result.reason is RefusalReason.TERMINAL
            return
        assert not before.is_terminal, f"terminal {before.status} mudou com {event}"
        # Fencing (3.4): só a tentativa da geração atual escreve.
        stale = getattr(event, "generation", before.generation) != before.generation
        assert not stale, f"geração vencida aceita: {event}"
        if isinstance(event, ExecutorStarted) or (
            isinstance(event, LeaseExpired) and event.executor_ran
        ):
            self.physical_generations.add(event.generation)
        for effect in result.effects:
            match effect:
                case StartAttempt():
                    self.started_attempts += 1
                case EndAttempt():
                    self.ended_attempts += 1
                case PublishOutputs():
                    # O prazo vencido impede a publicação (3.4).
                    assert before.deadline_at is not None
                    assert self.now <= before.deadline_at
                    self.published = True
                case ReleaseReservations():
                    self.releases += 1
                case _:
                    pass
        if result.run.phase is Phase.RETRY_WAIT and before.phase is not Phase.RETRY_WAIT:
            # Retentativa só dentro do prazo (3.4).
            assert before.deadline_at is not None
            assert self.now <= before.deadline_at
        self.run = result.run
        self.transitions += 1
        if self.first_stop_reason is None:
            self.first_stop_reason = self.run.stop_reason

    def current(self, offset: int) -> int:
        return self.run.generation + offset

    @rule(step=clock_steps)
    def advance_clock(self, step: timedelta) -> None:
        self.now += step

    @rule()
    def claim(self) -> None:
        self.decide(Claim(self.now))

    @rule(happens=rarely)
    def cancel(self, happens: bool) -> None:
        if happens:
            self.decide(CancelRequested())

    @rule()
    def expire_queue(self) -> None:
        self.decide(QueueExpired(self.now))

    @rule()
    def exceed_deadline(self) -> None:
        self.decide(DeadlineExceeded(self.now))

    @rule(offset=generation_offsets, phase=st.sampled_from(Phase))
    def reach_phase(self, offset: int, phase: Phase) -> None:
        self.decide(PhaseReached(self.current(offset), phase))

    @rule(happens=rarely, offset=generation_offsets, outcome=st.sampled_from(PublishOutcome))
    def publish(self, happens: bool, offset: int, outcome: PublishOutcome) -> None:
        if happens:
            self.decide(Published(self.current(offset), outcome, self.now))

    @rule(happens=rarely, offset=generation_offsets, code=st.sampled_from(DETERMINISTIC))
    def fail_deterministically(self, happens: bool, offset: int, code: ErrorCode) -> None:
        if happens:
            self.decide(DeterministicFailure(self.current(offset), code))

    @rule(offset=generation_offsets, jitter=jitters)
    def fail_transiently(self, offset: int, jitter: timedelta) -> None:
        self.decide(TransientFailure(self.current(offset), self.now, jitter))

    @rule(offset=generation_offsets)
    def start_executor(self, offset: int) -> None:
        self.decide(ExecutorStarted(self.current(offset)))

    @rule(offset=generation_offsets, executor_ran=st.booleans(), jitter=jitters)
    def expire_lease(self, offset: int, executor_ran: bool, jitter: timedelta) -> None:
        self.decide(LeaseExpired(self.current(offset), self.now, executor_ran, jitter))

    @rule(jitter=jitters)
    def lose_worker_right_after_claim(self, jitter: timedelta) -> None:
        # Worker morto entre o claim e o pedido do container: o reconciliador acha o lease
        # vencido e nenhum executor. Juntar os passos torna o quinto claim (INFRA_POISON)
        # comum nas sequências geradas.
        self.decide(Claim(self.now))
        self.decide(LeaseExpired(self.run.generation, self.now, False, jitter))
        self.now += jitter

    @rule(offset=generation_offsets)
    def confirm_executor_stopped(self, offset: int) -> None:
        self.decide(ExecutorStopped(self.current(offset)))

    def teardown(self) -> None:
        # Guia a geração para sequências longas, onde moram retentativa, prazo e parada.
        target(self.transitions, label="transições aceitas")
        # E para muitos claims, onde mora o teto de claims (INFRA_POISON).
        target(self.run.claim_count, label="claims")

    @invariant()
    def at_most_two_physical_attempts(self) -> None:
        assert len(self.physical_generations) <= MAX_ATTEMPTS
        assert self.run.physical_attempts == len(self.physical_generations)

    @invariant()
    def never_two_live_attempts(self) -> None:
        live = self.started_attempts - self.ended_attempts
        assert live in {0, 1}
        assert (live == 1) == self.run.has_live_attempt

    @invariant()
    def canceled_never_has_outputs(self) -> None:
        if self.run.status is Status.CANCELED:
            assert not self.published
            assert not self.run.has_published_outputs

    @invariant()
    def first_stop_reason_wins(self) -> None:
        assert self.run.stop_reason == self.first_stop_reason
        if self.run.is_terminal and self.first_stop_reason is not None:
            assert self.run.error_code == self.first_stop_reason

    @invariant()
    def reservations_released_once_and_only_at_the_end(self) -> None:
        assert self.releases == (1 if self.run.is_terminal else 0)

    @invariant()
    def one_attempt_per_claim_and_at_most_four(self) -> None:
        assert self.run.claim_count == self.started_attempts
        assert self.started_attempts <= MAX_CLAIMS


RunLifecycle.TestCase.settings = settings(max_examples=1000, stateful_step_count=50, deadline=None)
test_run_lifecycle = RunLifecycle.TestCase
