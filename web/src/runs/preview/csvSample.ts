/**
 * A amostra CSV do preview (ADR 037): cabeçalho e até 100 registros, sempre com vírgula, aspas do
 * RFC 4180 e fórmulas já escapadas pelo servidor com um tab no início. O valor sai como texto, com
 * o tab, para a tela renderizá-lo como texto e nunca como HTML ou fórmula.
 */
export interface CsvSample {
  readonly header: readonly string[];
  readonly rows: readonly (readonly string[])[];
}

/** O mesmo teto da amostra no servidor (csv_janitor/preview.py, PREVIEW_MAX_RECORDS). */
export const MAX_SAMPLE_ROWS = 100;

const QUOTE = '"';
const COMMA = ",";
const LF = "\n";
const CR = "\r";

/**
 * Amostra lida, ou `null` se o texto estiver vazio ou não for CSV bem formado. A tabela sai
 * retangular: a largura é a do maior registro lido, cabeçalho incluído, e os mais curtos ganham
 * `""` no fim. Assim toda célula tem um `<th>` na coluna; o rótulo do nome em branco é da tela.
 */
export function parseCsvSample(text: string): CsvSample | null {
  const records = readRecords(text);
  const [header, ...rest] = records ?? [];
  if (header === undefined) {
    return null;
  }
  const rows = rest.slice(0, MAX_SAMPLE_ROWS);
  const width = Math.max(header.length, ...rows.map((row) => row.length));
  return { header: padTo(header, width), rows: rows.map((row) => padTo(row, width)) };
}

function padTo(fields: readonly string[], width: number): string[] {
  return [...fields, ...Array.from({ length: width - fields.length }, () => "")];
}

/**
 * Registros do texto, ou `null` para aspas sem fechar ou aspas fora do lugar. Lê no máximo o
 * cabeçalho e `MAX_SAMPLE_ROWS` registros; o resto não aparece na tela.
 */
function readRecords(text: string): string[][] | null {
  const records: string[][] = [];
  let index = 0;
  while (index < text.length && records.length <= MAX_SAMPLE_ROWS) {
    const record = readRecord(text, index);
    if (record === null) {
      return null;
    }
    records.push(record.fields);
    index = record.next;
  }
  return records;
}

interface Read<T> {
  readonly fields: T;
  readonly next: number;
}

function readRecord(text: string, start: number): Read<string[]> | null {
  const fields: string[] = [];
  let index = start;
  for (;;) {
    const field = text[index] === QUOTE ? readQuoted(text, index) : readPlain(text, index);
    if (field === null) {
      return null;
    }
    fields.push(field.fields);
    index = field.next;
    if (text[index] !== COMMA) {
      return { fields, next: skipLineBreak(text, index) };
    }
    index += 1;
  }
}

function readPlain(text: string, start: number): Read<string> | null {
  let end = start;
  while (end < text.length && !isFieldEnd(text, end)) {
    if (text[end] === QUOTE) {
      return null;
    }
    end += 1;
  }
  return { fields: text.slice(start, end), next: end };
}

function readQuoted(text: string, start: number): Read<string> | null {
  let value = "";
  let index = start + 1;
  while (index < text.length) {
    const quote = text.indexOf(QUOTE, index);
    if (quote === -1) {
      return null;
    }
    value += text.slice(index, quote);
    if (text[quote + 1] === QUOTE) {
      value += QUOTE;
      index = quote + 2;
      continue;
    }
    const next = quote + 1;
    return next === text.length || isFieldEnd(text, next) ? { fields: value, next } : null;
  }
  return null;
}

function isFieldEnd(text: string, index: number): boolean {
  const char = text[index];
  return char === COMMA || char === LF || char === CR;
}

function skipLineBreak(text: string, index: number): number {
  if (text[index] === CR && text[index + 1] === LF) {
    return index + 2;
  }
  return index + 1;
}
