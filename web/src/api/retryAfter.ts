const DELTA_SECONDS = /^\d+$/;
const MS_PER_SECOND = 1_000;

/**
 * Espera pedida pelo header `Retry-After` (RFC 9110, 10.2.3), em milissegundos.
 *
 * Aceita delta-seconds e HTTP-date; data passada vira 0. Valor ausente ou inválido vira `null`,
 * e quem chama decide o intervalo padrão.
 */
export function parseRetryAfterMs(header: string | null, nowMs: number): number | null {
  if (header === null) {
    return null;
  }
  const value = header.trim();
  if (DELTA_SECONDS.test(value)) {
    return Number(value) * MS_PER_SECOND;
  }
  const dateMs = Date.parse(value);
  if (Number.isNaN(dateMs) || !value.endsWith("GMT")) {
    return null;
  }
  return Math.max(0, dateMs - nowMs);
}
