"""Prazo das esperas pelo agendador de alertas do OpenObserve e o diagnóstico quando ele estoura.

O agendador avalia uma quantidade limitada de alertas por vez. Com a frequência configurada de
1 minuto, cada alerta só volta depois que os outros passaram, e a volta cresce com o número de
alertas provisionados. O modelo é linear, ancorado na pior medição de referência, e nunca fica
abaixo da frequência configurada.
"""

from collections.abc import Iterable, Sequence
from itertools import pairwise

EVALUATION_MINUTES = 1
EVALUATION_S = EVALUATION_MINUTES * 60.0
# Medição de referência (CI, OpenObserve v1.1.2): com 38 alertas provisionados, cada alerta voltou
# a cada 110-120 s. Fica o pior valor, dividido por alerta, para a volta crescer junto com o
# catálogo quando entrar um alerta novo.
REFERENCE_ALERTS = 38
REFERENCE_ROUND_S = 120.0
SECONDS_PER_ALERT = REFERENCE_ROUND_S / REFERENCE_ALERTS
# Dois heartbeats depois de um instante qualquer custam até duas voltas: a primeira pode ter
# acabado de passar. A folga de 50% cobre a variação da vazão entre runners.
ROUNDS = 2
SAFETY_FACTOR = 1.5
# O job do CI (`openobserve` em .github/workflows/ci.yml) gasta, além das duas esperas, o setup
# (checkout, uv sync, subir OpenObserve e Collector) e os outros testes do marker: cerca de 1 min
# no pior caso medido na revisão do PR #296. Reserva-se o dobro, pela variação entre runners.
JOB_OVERHEAD_S = 120.0


def scheduler_round_s(alert_count: int) -> float:
    """Quanto um alerta espera até ser avaliado de novo, com `alert_count` alertas provisionados."""
    if alert_count < 1:
        raise ValueError(f"o cenário precisa de pelo menos um alerta, recebeu {alert_count}")
    return max(EVALUATION_S, alert_count * SECONDS_PER_ALERT)


def two_rounds_timeout_s(alert_count: int) -> float:
    """Prazo para duas avaliações do mesmo alerta: duas voltas com 50% de folga."""
    return ROUNDS * scheduler_round_s(alert_count) * SAFETY_FACTOR


def heartbeat_report(
    received: Sequence[float], after: float, alert_count: int, timeout_s: float
) -> str:
    """Prazo usado, a fórmula, os heartbeats relativos a `after` e a duração das voltas."""
    round_s = scheduler_round_s(alert_count)
    instants = sorted(received)
    late = [t for t in instants if t >= after]
    intervals = [later - earlier for earlier, later in pairwise(instants)]
    offsets = _listed(f"{t - after:+.1f} s" for t in instants)
    rounds = _listed(f"{interval:.1f} s" for interval in intervals)
    return (
        f"prazo de {timeout_s:.0f} s (fórmula: {ROUNDS} voltas x {round_s:.0f} s x {SAFETY_FACTOR}"
        f" = {two_rounds_timeout_s(alert_count):.0f} s, com volta de {alert_count} alertas x"
        f" {SECONDS_PER_ALERT:.2f} s e mínimo de {EVALUATION_S:.0f} s);"
        f" {len(late)} heartbeat(s) depois do marco;"
        f" instantes desde o provisionamento, relativos ao marco: {offsets};"
        f" voltas observadas (intervalo entre heartbeats): {rounds}"
    )


def _listed(items: Iterable[str]) -> str:
    text = ", ".join(items)
    return text or "nenhum"
