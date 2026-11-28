// O prazo dos resultados das execuções semeadas, relativo ao relógio real: a API falsa não manda
// `Date`, então o app compara com o relógio do aparelho, e uma data fixa já teria vencido.

const HOUR_MS = 3_600_000;

/** Prazo padrão: o selo aparece no tom neutro. */
export const RESULTS_LEFT_MS = 24 * HOUR_MS;
/** Prazo curto (dentro das 6 h finais): o selo aparece no tom de aviso. */
export const RESULTS_URGENT_LEFT_MS = 3 * HOUR_MS;

/** O instante ISO que fica `leftMs` depois de `nowMs`. */
export function resultsExpiresAt(nowMs: number, leftMs: number): string {
  return new Date(nowMs + leftMs).toISOString();
}
