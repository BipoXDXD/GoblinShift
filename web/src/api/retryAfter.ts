const DELTA_SECONDS = /^\d+$/;
const MS_PER_SECOND = 1_000;
// Acima de 2^31 - 1 ms o setTimeout dispara na hora; 5 min já cobre qualquer pausa real da API.
const MAX_RETRY_AFTER_MS = 5 * 60 * MS_PER_SECOND;

/**
 * Espera pedida pelo header `Retry-After` (RFC 9110, 10.2.3), em milissegundos.
 *
 * Aceita delta-seconds e HTTP-date; data passada vira 0 e espera acima de 5 min vira 5 min. Valor
 * ausente ou inválido vira `null`. Quem chama decide o intervalo padrão e o piso, para que um 0
 * não vire repetição imediata.
 */
export function parseRetryAfterMs(header: string | null, nowMs: number): number | null {
  if (header === null) {
    return null;
  }
  const value = header.trim();
  if (DELTA_SECONDS.test(value)) {
    return Math.min(Number(value) * MS_PER_SECOND, MAX_RETRY_AFTER_MS);
  }
  const dateMs = Date.parse(value);
  if (Number.isNaN(dateMs) || !value.endsWith("GMT")) {
    return null;
  }
  return Math.min(Math.max(0, dateMs - nowMs), MAX_RETRY_AFTER_MS);
}
