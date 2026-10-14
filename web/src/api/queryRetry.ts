import { ApiError, type ApiFailure } from "./response";

/** Tentativas a mais depois da primeira; o mesmo padrão do TanStack Query. */
const MAX_RETRIES = 3;
const SERVER_ERROR_MIN = 500;

/**
 * Política de retry das leituras (`retry` do QueryClient): só falha transitória (rede, timeout,
 * 5xx) tenta de novo. Um 4xx não muda repetindo, e esperar as tentativas atrasaria a resposta
 * certa, como o aviso de login em breve diante do 401.
 */
export function shouldRetryQuery(failureCount: number, error: Error): boolean {
  return failureCount < MAX_RETRIES && error instanceof ApiError && isTransient(error.failure);
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
