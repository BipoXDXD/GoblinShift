import { TEXT } from "./text";

const UNITS = ["byte", "kilobyte", "megabyte", "gigabyte"] as const;
const STEP = 1_000;

/** Tamanho em bytes com unidade SI (kB = 1000 bytes), no formato do idioma da interface. */
export function formatBytes(bytes: number): string {
  let value = bytes;
  let unit: (typeof UNITS)[number] = "byte";
  for (const next of UNITS.slice(1)) {
    if (value < STEP) {
      break;
    }
    value /= STEP;
    unit = next;
  }
  return new Intl.NumberFormat(TEXT.locale, {
    style: "unit",
    unit,
    maximumFractionDigits: 1,
  }).format(value);
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
