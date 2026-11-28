import { TEXT } from "./text";

// Base 1024 com rótulo sem "i" (ADR 062): "50 MB" é o teto de 50 MiB, e nenhum arquivo que o
// sistema mostre com até "50 MB" (base 1000 no Finder) passa dele.
const UNITS = ["KB", "MB", "GB", "TB", "PB"] as const;
const STEP = 1_024;
const exactBytes = new Intl.NumberFormat(TEXT.locale, { style: "unit", unit: "byte" });
const roundedSize = new Intl.NumberFormat(TEXT.locale, { maximumFractionDigits: 1 });

/** Tamanho em base 1024, rotulado KB, MB e GB em toda a interface. */
export function formatBytes(bytes: number): string {
  if (bytes < STEP) {
    return exactBytes.format(bytes);
  }
  let value = bytes / STEP;
  let unit: (typeof UNITS)[number] = UNITS[0];
  for (const next of UNITS.slice(1)) {
    if (value < STEP) {
      break;
    }
    value /= STEP;
    unit = next;
  }
  return `${roundedSize.format(value)} ${unit}`;
}

/**
 * Exibe os bytes exatos se o arredondamento esconder o excesso, sem perder o teto arredondado
 * que a interface anuncia ("50 MB (52.428.800 bytes)").
 */
export function formatByteComparison(bytes: number, limit: number): readonly [string, string] {
  const size = formatBytes(bytes);
  const max = formatBytes(limit);
  return size === max
    ? [exactBytes.format(bytes), `${max} (${exactBytes.format(limit)})`]
    : [size, max];
}

/** Instante (ISO 8601 com fuso) no fuso e no formato da pessoa. */
export function formatDateTime(isoInstant: string): string {
  return new Intl.DateTimeFormat(TEXT.locale, { dateStyle: "short", timeStyle: "short" }).format(
    new Date(isoInstant),
  );
}

/** Contagem com separador de milhar do idioma da interface ("1.200"). */
export function formatCount(count: number): string {
  return new Intl.NumberFormat(TEXT.locale).format(count);
}
