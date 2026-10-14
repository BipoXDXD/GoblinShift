import { ApiError } from "../api/response";
import type { RunStatus } from "./api";

/** Intervalo de polling de 2.2 quando a resposta não traz Retry-After, e o mínimo entre consultas. */
const DEFAULT_POLL_MS = 2_000;
const MAX_BACKOFF_MS = 30_000;
const JITTER_FRACTION = 0.1;

const TERMINAL_STATUSES: ReadonlySet<RunStatus> = new Set([
  "succeeded",
  "succeeded_with_warnings",
  "canceled",
  "failed",
]);

// Falhas que se repetiriam igual: sem sessão, sem permissão, execução inexistente ou expirada.
const PERMANENT_HTTP_STATUSES: ReadonlySet<number> = new Set([401, 403, 404, 410]);

/** O que a última consulta deixou: o status da execução, ou a falha e quantas vieram seguidas. */
export type PollSnapshot =
  | { kind: "success"; status: RunStatus; retryAfterMs: number | null }
  | { kind: "failure"; error: unknown; consecutiveFailures: number };

/**
 * Espera até a próxima consulta, ou `false` para parar.
 *
 * Nunca volta antes do Retry-After nem de 2 s: um Retry-After 0 (ou de data passada) viraria
 * `refetchInterval` 0, que o TanStack Query entende como "não consultar mais". Em falha
 * transitória recua exponencialmente (2, 4, 8... até 30 s), com jitter para cima; para no estado
 * terminal e em falha que repetir não resolve.
 */
export function nextPollDelayMs(snapshot: PollSnapshot, random: () => number): number | false {
  if (snapshot.kind === "success") {
    return TERMINAL_STATUSES.has(snapshot.status)
      ? false
      : Math.max(snapshot.retryAfterMs ?? 0, DEFAULT_POLL_MS);
  }
  const { error, consecutiveFailures } = snapshot;
  if (!(error instanceof ApiError) || isPermanent(error)) {
    return false;
  }
  const backoffMs = Math.min(DEFAULT_POLL_MS * 2 ** (consecutiveFailures - 1), MAX_BACKOFF_MS);
  const jitteredMs = backoffMs + Math.floor(backoffMs * JITTER_FRACTION * random());
  const { failure } = error;
  const retryAfterMs = "retryAfterMs" in failure ? failure.retryAfterMs : null;
  return Math.max(jitteredMs, retryAfterMs ?? 0);
}

function isPermanent({ failure }: ApiError): boolean {
  return failure.kind === "problem" && PERMANENT_HTTP_STATUSES.has(failure.status);
}
