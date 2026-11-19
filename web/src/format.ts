import { TEXT } from "./text";

const UNITS = ["KiB", "MiB", "GiB", "TiB", "PiB"] as const;
const STEP = 1_024;
const exactBytes = new Intl.NumberFormat(TEXT.locale, { style: "unit", unit: "byte" });
const roundedSize = new Intl.NumberFormat(TEXT.locale, { maximumFractionDigits: 1 });

/** Tamanho com unidades binárias, como os limites da API e dos Termos. */
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

/** Exibe os bytes exatos se o arredondamento esconder o excesso. */
export function formatByteComparison(bytes: number, limit: number): readonly [string, string] {
  const size = formatBytes(bytes);
  const max = formatBytes(limit);
  return size === max ? [exactBytes.format(bytes), exactBytes.format(limit)] : [size, max];
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
