import { ApiError, type ApiFailure } from "./response";

/** Tentativas a mais depois da primeira; o mesmo padrão do TanStack Query. */
const MAX_RETRIES = 3;
const TIMEOUT_RETRIES = 1;
const INITIAL_RETRY_DELAY_MS = 1_000;
const SERVER_ERROR_MIN = 500;

/**
 * Política de retry das leituras (`retry` do QueryClient): só falha transitória (rede, timeout,
 * 5xx) tenta de novo. Um 4xx não muda repetindo, e esperar as tentativas atrasaria a resposta
 * certa, como o convite para entrar diante do 401.
 */
export function shouldRetryQuery(failureCount: number, error: Error): boolean {
  if (!(error instanceof ApiError)) {
    return false;
  }
  const limit = error.failure.kind === "timeout" ? TIMEOUT_RETRIES : MAX_RETRIES;
  return failureCount < limit && isTransient(error.failure);
}

/** Adia leituras transitórias e respeita o intervalo pedido pelo servidor. */
export function queryRetryDelay(failureCount: number, error: Error): number {
  const delay = INITIAL_RETRY_DELAY_MS * 2 ** failureCount;
  const jitteredDelay = delay + Math.floor(Math.random() * (delay / 2));
  const retryAfter =
    error instanceof ApiError && "retryAfterMs" in error.failure
      ? error.failure.retryAfterMs
      : null;
  return Math.max(jitteredDelay, retryAfter ?? 0);
}

function isTransient(failure: ApiFailure): boolean {
  switch (failure.kind) {
    case "network":
    case "timeout":
      return true;
    case "problem":
    case "unexpected-response":
      return failure.status >= SERVER_ERROR_MIN;
  }
}
