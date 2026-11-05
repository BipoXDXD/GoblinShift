// Decisão do monitor de uptime, sem I/O: quando avisar que caiu e quando avisar que voltou.

// Uma falha isolada (rede, reinício da API) não avisa: com a cron de 2 min, são cerca de 4 min.
export const FAILURES_BEFORE_ALERT = 2;

export const INITIAL_STATE = Object.freeze({ failures: 0, alertedAt: null });

/** Devolve o estado seguinte e, se for o caso, a notificação: `down`, `up` ou null. */
export function advance(state, healthy, nowMs) {
  if (healthy) {
    return { state: INITIAL_STATE, notify: state.alertedAt === null ? null : 'up' };
  }
  const failures = state.failures + 1;
  if (state.alertedAt === null && failures >= FAILURES_BEFORE_ALERT) {
    return { state: { failures, alertedAt: nowMs }, notify: 'down' };
  }
  return { state: { ...state, failures }, notify: null };
}

/** Texto da notificação; `state` é o estado anterior a `advance` quando o serviço voltou. */
export function describe(notify, state, detail, nowMs, target) {
  if (notify === 'down') {
    return `[GoblinShift] FORA DO AR: ${target} respondeu ${detail}`;
  }
  const minutes = Math.round((nowMs - state.alertedAt) / 60_000);
  return `[GoblinShift] VOLTOU: ${target} responde 200 (cerca de ${minutes} min desde o aviso)`;
}
