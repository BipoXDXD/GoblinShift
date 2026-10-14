"""Tabela de transições da execução (docs/GoblinShiftPlan.md, 3.5), linha a linha."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from goblinshift.runs.lifecycle import (
    CancelRequested,
    Claim,
    DeadlineExceeded,
    DeterministicFailure,
    DiscardCandidates,
    EndAttempt,
    ErrorCode,
    Event,
    ExecutorStopped,
    InconsistentRunStateError,
    Phase,
    PhaseReached,
    Published,
    PublishOutcome,
    PublishOutputs,
    QueueExpired,
    RefusalReason,
    Refused,
    ReleaseReservations,
    RequestStop,
    RunState,
    StartAttempt,
    Status,
    TransientFailure,
    Transition,
    decide_transition,
)

ACCEPTED_AT = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)
QUEUE_EXPIRES_AT = ACCEPTED_AT + timedelta(hours=2)
STARTED_AT = ACCEPTED_AT + timedelta(minutes=5)
DEADLINE_AT = STARTED_AT + timedelta(minutes=30)
DURING = STARTED_AT + timedelta(minutes=3)
AFTER_DEADLINE = DEADLINE_AT + timedelta(seconds=1)
JITTER = timedelta(seconds=12)
ONE_MICROSECOND = timedelta(microseconds=1)


def queued() -> RunState:
    return RunState.queued(ACCEPTED_AT)


def running(*, attempts: int = 1, phase: Phase = Phase.PROCESSING) -> RunState:
    return replace(
        queued(),
        status=Status.RUNNING,
        phase=phase,
        generation=attempts,
        claim_count=attempts,
        attempts_used=attempts,
        deadline_at=DEADLINE_AT,
    )


def retry_waiting(*, claim_count: int = 1, eligible_at: datetime = DURING) -> RunState:
    return replace(
        running(phase=Phase.RETRY_WAIT),
        generation=claim_count,
        claim_count=claim_count,
        eligible_at=eligible_at,
    )


def stopping(reason: ErrorCode, *, cancel_requested: bool = False) -> RunState:
    return replace(
        running(),
        status=Status.STOPPING,
        phase=None,
        stop_reason=reason,
        cancel_requested=cancel_requested,
    )


def accepted(run: RunState, event: Event) -> Transition:
    result = decide_transition(run, event)
    assert isinstance(result, Transition), result
    return result


def refusal(run: RunState, event: Event) -> RefusalReason:
    result = decide_transition(run, event)
    assert isinstance(result, Refused), result
    return result.reason


# Linha 1: queued + claim → running/inspecting.


def test_claim_na_fila_inicia_a_primeira_tentativa_e_fixa_o_prazo() -> None:
    result = accepted(queued(), Claim(STARTED_AT))

    run = result.run
    assert (run.status, run.phase) == (Status.RUNNING, Phase.INSPECTING)
    assert (run.generation, run.claim_count, run.attempts_used) == (1, 1, 1)
    assert run.deadline_at == STARTED_AT + timedelta(minutes=30)
    assert result.effects == (StartAttempt(generation=1, number=1),)


def test_claim_antes_de_eligible_at_e_recusado() -> None:
    assert refusal(queued(), Claim(ACCEPTED_AT - ONE_MICROSECOND)) is RefusalReason.NOT_ELIGIBLE


def test_claim_no_instante_de_eligible_at_e_aceito() -> None:
    assert accepted(queued(), Claim(ACCEPTED_AT)).run.status is Status.RUNNING


def test_claim_no_instante_em_que_a_fila_vence_e_recusado() -> None:
    assert refusal(queued(), Claim(QUEUE_EXPIRES_AT)) is RefusalReason.NOT_ELIGIBLE


def test_claim_um_instante_antes_de_a_fila_vencer_e_aceito() -> None:
    result = accepted(queued(), Claim(QUEUE_EXPIRES_AT - ONE_MICROSECOND))
    assert result.run.status is Status.RUNNING


# Linha 2: queued + cancelamento → canceled.


def test_cancelamento_na_fila_termina_cancelado_sem_tentativa() -> None:
    result = accepted(queued(), CancelRequested())

    run = result.run
    assert (run.status, run.phase, run.error_code) == (Status.CANCELED, None, ErrorCode.CANCELED)
    assert run.cancel_requested is True
    assert run.attempts_used == 0
    assert result.effects == (ReleaseReservations(),)


# Linha 3: queued + fila vencida → failed (QUEUE_EXPIRED).


def test_fila_vencida_termina_failed_com_queue_expired() -> None:
    result = accepted(queued(), QueueExpired(QUEUE_EXPIRES_AT + ONE_MICROSECOND))

    assert (result.run.status, result.run.error_code) == (Status.FAILED, ErrorCode.QUEUE_EXPIRED)
    assert result.run.stop_reason is ErrorCode.QUEUE_EXPIRED
    assert result.effects == (ReleaseReservations(),)


def test_fila_no_instante_exato_do_prazo_ainda_nao_venceu() -> None:
    assert refusal(queued(), QueueExpired(QUEUE_EXPIRES_AT)) is RefusalReason.NOT_YET_DUE


def test_fila_vencida_fora_da_fila_e_recusada() -> None:
    assert refusal(running(), QueueExpired(AFTER_DEADLINE)) is RefusalReason.INVALID_FOR_STATUS


# Linha 4: running + publicação → succeeded, succeeded_with_warnings ou failed.


@pytest.mark.parametrize(
    ("outcome", "status", "error_code"),
    [
        (PublishOutcome.SUCCEEDED, Status.SUCCEEDED, None),
        (PublishOutcome.SUCCEEDED_WITH_WARNINGS, Status.SUCCEEDED_WITH_WARNINGS, None),
        (PublishOutcome.NO_VALID_OUTPUTS, Status.FAILED, ErrorCode.NO_VALID_OUTPUTS),
    ],
)
def test_publicacao_da_geracao_atual_no_prazo_termina_a_execucao(
    outcome: PublishOutcome, status: Status, error_code: ErrorCode | None
) -> None:
    result = accepted(running(), Published(generation=1, outcome=outcome, now=DURING))

    assert (result.run.status, result.run.phase, result.run.error_code) == (
        status,
        None,
        error_code,
    )
    assert result.run.has_published_outputs is True
    assert result.effects == (PublishOutputs(1), EndAttempt(1), ReleaseReservations())


def test_publicacao_no_instante_exato_do_prazo_e_aceita() -> None:
    event = Published(generation=1, outcome=PublishOutcome.SUCCEEDED, now=DEADLINE_AT)
    assert accepted(running(), event).run.status is Status.SUCCEEDED


def test_publicacao_depois_do_prazo_e_recusada() -> None:
    event = Published(generation=1, outcome=PublishOutcome.SUCCEEDED, now=AFTER_DEADLINE)
    assert refusal(running(), event) is RefusalReason.DEADLINE_PASSED


def test_publicacao_de_geracao_vencida_e_recusada() -> None:
    event = Published(generation=1, outcome=PublishOutcome.SUCCEEDED, now=DURING)
    assert refusal(running(attempts=2), event) is RefusalReason.STALE_GENERATION


def test_publicacao_depois_do_cancelamento_e_recusada() -> None:
    run = stopping(ErrorCode.CANCELED, cancel_requested=True)
    event = Published(generation=1, outcome=PublishOutcome.SUCCEEDED, now=DURING)
    assert refusal(run, event) is RefusalReason.INVALID_FOR_STATUS


def test_publicacao_em_retry_wait_e_recusada() -> None:
    event = Published(generation=1, outcome=PublishOutcome.SUCCEEDED, now=DURING)
    assert refusal(retry_waiting(), event) is RefusalReason.INVALID_FOR_STATUS


# Linha 5: running + falha determinística → failed (código da falha).


def test_falha_deterministica_termina_failed_com_o_codigo_e_descarta_candidatos() -> None:
    event = DeterministicFailure(generation=1, code=ErrorCode.RESOURCE_LIMIT_EXCEEDED)
    result = accepted(running(), event)

    assert (result.run.status, result.run.error_code) == (
        Status.FAILED,
        ErrorCode.RESOURCE_LIMIT_EXCEEDED,
    )
    assert result.run.has_published_outputs is False
    assert result.effects == (EndAttempt(1), DiscardCandidates(1), ReleaseReservations())


def test_falha_deterministica_de_geracao_vencida_e_recusada() -> None:
    event = DeterministicFailure(generation=1, code=ErrorCode.OUTPUT_LIMIT_EXCEEDED)
    assert refusal(running(attempts=2), event) is RefusalReason.STALE_GENERATION


@pytest.mark.parametrize(
    "code", [ErrorCode.EXECUTOR_UNAVAILABLE, ErrorCode.CANCELED, ErrorCode.NO_VALID_OUTPUTS]
)
def test_falha_deterministica_com_codigo_de_outra_classe_nao_existe(code: ErrorCode) -> None:
    with pytest.raises(ValueError, match="determinístic"):
        DeterministicFailure(generation=1, code=code)


# Linha 6: running + falha transitória com tentativa e prazo → running/retry_wait.


def test_falha_transitoria_com_tentativa_restante_espera_o_jitter() -> None:
    result = accepted(running(), TransientFailure(generation=1, now=DURING, jitter=JITTER))

    run = result.run
    assert (run.status, run.phase) == (Status.RUNNING, Phase.RETRY_WAIT)
    assert run.eligible_at == DURING + JITTER
    assert run.stop_reason is None
    assert result.effects == (EndAttempt(1),)


@pytest.mark.parametrize("seconds", [5, 30])
def test_jitter_nas_bordas_e_aceito(seconds: int) -> None:
    TransientFailure(generation=1, now=DURING, jitter=timedelta(seconds=seconds))


@pytest.mark.parametrize(
    "jitter",
    [timedelta(seconds=5) - ONE_MICROSECOND, timedelta(seconds=30) + ONE_MICROSECOND],
)
def test_jitter_fora_da_faixa_nao_existe(jitter: timedelta) -> None:
    with pytest.raises(ValueError, match="jitter"):
        TransientFailure(generation=1, now=DURING, jitter=jitter)


def test_falha_transitoria_de_geracao_vencida_e_recusada() -> None:
    event = TransientFailure(generation=1, now=DURING, jitter=JITTER)
    assert refusal(running(attempts=2), event) is RefusalReason.STALE_GENERATION


# Linha 7: retry_wait + claim → running/inspecting.


def test_claim_em_retry_wait_inicia_a_segunda_tentativa_sem_mudar_o_prazo() -> None:
    result = accepted(retry_waiting(), Claim(DURING))

    run = result.run
    assert (run.status, run.phase) == (Status.RUNNING, Phase.INSPECTING)
    assert (run.generation, run.claim_count, run.attempts_used) == (2, 2, 2)
    assert run.deadline_at == DEADLINE_AT
    assert result.effects == (StartAttempt(generation=2, number=2),)


def test_claim_em_retry_wait_antes_do_jitter_e_recusado() -> None:
    assert refusal(retry_waiting(), Claim(DURING - ONE_MICROSECOND)) is RefusalReason.NOT_ELIGIBLE


def test_claim_em_retry_wait_depois_do_prazo_e_recusado() -> None:
    assert refusal(retry_waiting(), Claim(AFTER_DEADLINE)) is RefusalReason.DEADLINE_PASSED


def test_claim_com_tentativa_viva_e_recusado() -> None:
    assert refusal(running(), Claim(DURING)) is RefusalReason.INVALID_FOR_STATUS


# Linha 8: retry_wait + cancelamento ou prazo, sem executor vivo → terminal direto.


def test_cancelamento_em_retry_wait_termina_cancelado_sem_pedir_parada() -> None:
    result = accepted(retry_waiting(), CancelRequested())

    assert (result.run.status, result.run.phase) == (Status.CANCELED, None)
    assert result.effects == (ReleaseReservations(),)


def test_prazo_vencido_em_retry_wait_termina_failed_sem_pedir_parada() -> None:
    result = accepted(retry_waiting(), DeadlineExceeded(AFTER_DEADLINE))

    assert (result.run.status, result.run.error_code) == (
        Status.FAILED,
        ErrorCode.DEADLINE_EXCEEDED,
    )
    assert result.effects == (ReleaseReservations(),)


def test_prazo_no_instante_exato_ainda_nao_venceu() -> None:
    assert refusal(running(), DeadlineExceeded(DEADLINE_AT)) is RefusalReason.NOT_YET_DUE


def test_prazo_vencido_na_fila_e_recusado_porque_o_prazo_ainda_nao_existe() -> None:
    assert refusal(queued(), DeadlineExceeded(AFTER_DEADLINE)) is RefusalReason.INVALID_FOR_STATUS


# Linha 9: running com executor possivelmente vivo → stopping.


def test_cancelamento_com_executor_vivo_pede_parada() -> None:
    result = accepted(running(), CancelRequested())

    run = result.run
    assert (run.status, run.phase, run.stop_reason) == (
        Status.STOPPING,
        None,
        ErrorCode.CANCELED,
    )
    assert run.cancel_requested is True
    assert result.effects == (RequestStop(1),)


def test_prazo_vencido_com_executor_vivo_pede_parada() -> None:
    result = accepted(running(), DeadlineExceeded(AFTER_DEADLINE))

    assert (result.run.status, result.run.stop_reason) == (
        Status.STOPPING,
        ErrorCode.DEADLINE_EXCEEDED,
    )
    assert result.effects == (RequestStop(1),)


def test_falha_transitoria_sem_tentativa_restante_pede_parada() -> None:
    event = TransientFailure(generation=2, now=DURING, jitter=JITTER)
    result = accepted(running(attempts=2), event)

    assert (result.run.status, result.run.stop_reason) == (
        Status.STOPPING,
        ErrorCode.EXECUTOR_UNAVAILABLE,
    )
    assert result.effects == (RequestStop(2),)


def test_falha_transitoria_depois_do_prazo_para_por_prazo() -> None:
    event = TransientFailure(generation=1, now=AFTER_DEADLINE, jitter=JITTER)
    result = accepted(running(), event)

    assert (result.run.status, result.run.stop_reason) == (
        Status.STOPPING,
        ErrorCode.DEADLINE_EXCEEDED,
    )


def test_cancelamento_depois_do_prazo_registra_a_intencao_mas_o_prazo_vence() -> None:
    result = accepted(stopping(ErrorCode.DEADLINE_EXCEEDED), CancelRequested())

    assert result.run.stop_reason is ErrorCode.DEADLINE_EXCEEDED
    assert result.run.cancel_requested is True
    assert result.effects == ()


def test_prazo_depois_do_cancelamento_nao_troca_a_razao_de_parada() -> None:
    run = stopping(ErrorCode.CANCELED, cancel_requested=True)
    result = accepted(run, DeadlineExceeded(AFTER_DEADLINE))

    assert result.run == run
    assert result.effects == ()


# Linha 10: stopping + término confirmado → canceled ou failed.


@pytest.mark.parametrize(
    ("reason", "status"),
    [
        (ErrorCode.CANCELED, Status.CANCELED),
        (ErrorCode.DEADLINE_EXCEEDED, Status.FAILED),
        (ErrorCode.EXECUTOR_UNAVAILABLE, Status.FAILED),
    ],
)
def test_termino_confirmado_conclui_pela_razao_de_parada(reason: ErrorCode, status: Status) -> None:
    run = stopping(reason, cancel_requested=reason is ErrorCode.CANCELED)
    result = accepted(run, ExecutorStopped(generation=1))

    assert (result.run.status, result.run.error_code) == (status, reason)
    assert result.run.has_published_outputs is False
    assert result.effects == (EndAttempt(1), DiscardCandidates(1), ReleaseReservations())


def test_primeira_razao_de_parada_decide_mesmo_com_cancelamento_posterior() -> None:
    after_cancel = accepted(stopping(ErrorCode.DEADLINE_EXCEEDED), CancelRequested()).run
    result = accepted(after_cancel, ExecutorStopped(generation=1))

    assert (result.run.status, result.run.error_code) == (
        Status.FAILED,
        ErrorCode.DEADLINE_EXCEEDED,
    )


def test_termino_confirmado_de_geracao_vencida_e_recusado() -> None:
    run = replace(stopping(ErrorCode.CANCELED, cancel_requested=True), generation=2)
    assert refusal(run, ExecutorStopped(generation=1)) is RefusalReason.STALE_GENERATION


def test_termino_confirmado_sem_parada_pedida_e_recusado() -> None:
    assert refusal(running(), ExecutorStopped(generation=1)) is RefusalReason.INVALID_FOR_STATUS


# Linha 11: claim com claims = 4 → failed (INFRA_POISON).


@pytest.mark.parametrize(
    "run",
    [
        replace(queued(), claim_count=4, generation=4),
        retry_waiting(claim_count=4),
    ],
    ids=["queued", "retry_wait"],
)
def test_quinto_claim_termina_failed_com_infra_poison(run: RunState) -> None:
    result = accepted(run, Claim(DURING))

    assert (result.run.status, result.run.error_code) == (Status.FAILED, ErrorCode.INFRA_POISON)
    assert result.run.claim_count == 4
    assert result.effects == (ReleaseReservations(),)


def test_quarto_claim_ainda_inicia_tentativa() -> None:
    result = accepted(retry_waiting(claim_count=3), Claim(DURING))

    assert result.run.claim_count == 4
    assert result.effects == (StartAttempt(generation=4, number=2),)


# Fases de trabalho e fencing da escrita de fase.


def test_fase_avanca_na_ordem_do_trabalho() -> None:
    result = accepted(running(), PhaseReached(generation=1, phase=Phase.PACKAGING))

    assert result.run.phase is Phase.PACKAGING
    assert result.effects == ()


@pytest.mark.parametrize("phase", [Phase.INSPECTING, Phase.PROCESSING, Phase.RETRY_WAIT])
def test_fase_que_nao_avanca_e_recusada(phase: Phase) -> None:
    event = PhaseReached(generation=1, phase=phase)
    assert refusal(running(), event) is RefusalReason.PHASE_NOT_FORWARD


def test_fase_de_geracao_vencida_e_recusada() -> None:
    event = PhaseReached(generation=1, phase=Phase.PACKAGING)
    assert refusal(running(attempts=2), event) is RefusalReason.STALE_GENERATION


# Estado terminal nunca muda.

TERMINAL_RUNS = [
    accepted(queued(), CancelRequested()).run,
    accepted(queued(), QueueExpired(AFTER_DEADLINE + timedelta(hours=2))).run,
    accepted(running(), Published(1, PublishOutcome.SUCCEEDED, DURING)).run,
    accepted(running(), Published(1, PublishOutcome.SUCCEEDED_WITH_WARNINGS, DURING)).run,
]

ALL_EVENTS: list[Event] = [
    Claim(DURING),
    CancelRequested(),
    QueueExpired(AFTER_DEADLINE + timedelta(hours=2)),
    DeadlineExceeded(AFTER_DEADLINE),
    PhaseReached(1, Phase.FINALIZING),
    Published(1, PublishOutcome.SUCCEEDED, DURING),
    DeterministicFailure(1, ErrorCode.OUTPUT_LIMIT_EXCEEDED),
    TransientFailure(1, DURING, JITTER),
    ExecutorStopped(1),
]


@pytest.mark.parametrize("run", TERMINAL_RUNS, ids=lambda run: str(run.status))
@pytest.mark.parametrize("event", ALL_EVENTS, ids=lambda event: type(event).__name__)
def test_estado_terminal_recusa_qualquer_evento(run: RunState, event: Event) -> None:
    assert refusal(run, event) is RefusalReason.TERMINAL


# Fronteira: instantes sem fuso e snapshots incoerentes não existem.


def test_instante_sem_fuso_nao_existe() -> None:
    with pytest.raises(ValueError, match="fuso"):
        Claim(datetime(2026, 10, 13, 12, 0))


INCONSISTENT_SNAPSHOTS = {
    "fase_fora_de_running": (queued(), {"phase": Phase.INSPECTING}, "fase só existe"),
    "retry_wait_sem_tentativa_restante": (
        retry_waiting(),
        {"attempts_used": 2, "claim_count": 2, "generation": 2},
        "retry_wait sem tentativa",
    ),
    "mais_tentativas_que_claims": (queued(), {"attempts_used": 1}, "tentativas fora"),
    "claims_acima_do_teto": (queued(), {"claim_count": 5, "generation": 5}, "claims acima"),
    "tentativa_sem_prazo": (
        queued(),
        {"attempts_used": 1, "claim_count": 1, "generation": 1},
        "o prazo existe",
    ),
    "fila_com_tentativa": (
        queued(),
        {"attempts_used": 1, "claim_count": 1, "generation": 1, "deadline_at": DEADLINE_AT},
        "na fila com tentativa",
    ),
    "razao_de_parada_na_fila": (queued(), {"stop_reason": ErrorCode.CANCELED}, "razão de parada"),
    "stopping_sem_razao": (running(), {"status": Status.STOPPING, "phase": None}, "stopping sem"),
    "cancelamento_sem_parada": (queued(), {"cancel_requested": True}, "cancelamento pedido"),
    "erro_fora_do_fim": (queued(), {"error_code": ErrorCode.QUEUE_EXPIRED}, "código de erro"),
    "cancelado_com_outro_codigo": (
        accepted(queued(), CancelRequested()).run,
        {"error_code": ErrorCode.QUEUE_EXPIRED},
        "canceled sem",
    ),
    "cancelado_com_saida": (
        accepted(queued(), CancelRequested()).run,
        {"has_published_outputs": True},
        "saídas publicadas",
    ),
}


@pytest.mark.parametrize(
    ("base", "changes", "message"),
    INCONSISTENT_SNAPSHOTS.values(),
    ids=INCONSISTENT_SNAPSHOTS.keys(),
)
def test_snapshot_incoerente_e_rejeitado(
    base: RunState, changes: dict[str, object], message: str
) -> None:
    with pytest.raises(InconsistentRunStateError, match=message):
        replace(base, **changes)  # type: ignore[arg-type]
