import { ApiError } from "../api/response";

/** Envios simultâneos por aba: o servidor também limita as vagas de transferência por conta. */
export const MAX_CONCURRENT_UPLOADS = 2;

type Task<T> = () => Promise<T>;
type Limiter = <T>(task: Task<T>) => Promise<T>;

/** Fila que roda no máximo `limit` tarefas ao mesmo tempo, na ordem de chegada. */
export function createLimiter(limit: number): Limiter {
  if (!Number.isInteger(limit) || limit < 1) {
    throw new RangeError(`limite de concorrência inválido: ${String(limit)}`);
  }
  let active = 0;
  const waiting: (() => void)[] = [];

  // A vaga passa direto para quem espera, sem voltar a ficar livre: assim ninguém que chegue
  // entre a liberação e o despertar fura a fila nem passa do limite.
  const release = (): void => {
    const next = waiting.shift();
    if (next === undefined) {
      active -= 1;
    } else {
      next();
    }
  };

  return async <T>(task: Task<T>): Promise<T> => {
    if (active < limit) {
      active += 1;
    } else {
      await new Promise<void>((resolve) => waiting.push(resolve));
    }
    try {
      return await task();
    } finally {
      release();
    }
  };
}

export interface RateLimitPolicy {
  /** Tentativas no total, contando a primeira. */
  readonly maxAttempts: number;
  /** Espera quando o 429 vem sem Retry-After. */
  readonly fallbackDelayMs: number;
  /** Em [0, 1); acrescenta até 10% à espera, para as abas não voltarem juntas. */
  readonly random: () => number;
  readonly signal?: AbortSignal;
}

const JITTER_FRACTION = 0.1;

/**
 * Repete `attempt` enquanto a API responder RATE_LIMITED, esperando o Retry-After (nunca menos).
 *
 * Outras falhas, inclusive QUOTA_EXCEEDED, voltam na hora: esperar não as resolve.
 */
export async function retryOnRateLimit<T>(attempt: Task<T>, policy: RateLimitPolicy): Promise<T> {
  for (let attemptNumber = 1; ; attemptNumber += 1) {
    try {
      return await attempt();
    } catch (error: unknown) {
      const delayMs = rateLimitDelayMs(error, policy);
      if (delayMs === null || attemptNumber >= policy.maxAttempts) {
        throw error;
      }
      await sleep(delayMs, policy.signal);
    }
  }
}

function rateLimitDelayMs(error: unknown, policy: RateLimitPolicy): number | null {
  if (!(error instanceof ApiError) || error.failure.kind !== "problem") {
    return null;
  }
  const { problem, retryAfterMs } = error.failure;
  if (problem.code !== "RATE_LIMITED") {
    return null;
  }
  const baseMs = retryAfterMs ?? policy.fallbackDelayMs;
  return baseMs + Math.floor(baseMs * JITTER_FRACTION * policy.random());
}

function sleep(ms: number, signal: AbortSignal | undefined): Promise<void> {
  return new Promise((resolve, reject) => {
    signal?.throwIfAborted();
    const onAbort = (): void => {
      clearTimeout(timer);
      try {
        signal?.throwIfAborted();
      } catch (reason: unknown) {
        reject(reason instanceof Error ? reason : new Error(String(reason)));
      }
    };
    const timer = setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }, ms);
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}
