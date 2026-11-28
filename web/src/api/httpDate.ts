/**
 * Instante de uma HTTP-date (RFC 9110, 5.6.7), em ms desde a época; `null` se não for uma.
 *
 * O `Date.parse` aceita formatos demais (ISO 8601, datas sem fuso, que viram hora local); exigir o
 * `GMT` do fim deixa passar só o IMF-fixdate que a API manda. Critério único do `Date` (relógio do
 * servidor) e do `Retry-After`.
 */
export function parseHttpDateMs(value: string): number | null {
  const trimmed = value.trim();
  const ms = Date.parse(trimmed);
  if (Number.isNaN(ms) || !trimmed.endsWith("GMT")) {
    return null;
  }
  return ms;
}
