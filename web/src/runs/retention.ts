/** Com até tantas horas restantes, o prazo dos resultados vira aviso (âmbar), não informação. */
const URGENT_HOURS = 6;

const MINUTE_MS = 60_000;
const HOUR_MS = 60 * MINUTE_MS;

/**
 * Quanto falta para os resultados serem apagados: horas e minutos arredondados para cima, e
 * `urgent` com {@link URGENT_HOURS} ou menos. `null` se já venceu (ou a data não se lê): aí não há
 * prazo a dar.
 */
export function remainingLabel(
  expiresAtIso: string,
  nowMs: number,
): { readonly hours: number; readonly minutes: number; readonly urgent: boolean } | null {
  const left = Date.parse(expiresAtIso) - nowMs;
  if (!(left > 0)) {
    return null;
  }
  const hours = Math.ceil(left / HOUR_MS);
  return { hours, minutes: Math.ceil(left / MINUTE_MS), urgent: hours <= URGENT_HOURS };
}
