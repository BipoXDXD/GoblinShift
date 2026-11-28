import { TEXT } from "../../text";

// Leitor do campo "Páginas" do cartão do PDF Press v2 (ADR 070). A API recebe intervalos
// estruturados; este texto (`1-3, 7, 10-`) só existe no formulário. `last: null` é "até o fim",
// que só o executor resolve, porque só ele conhece a contagem de páginas de cada PDF.

export interface PageRangeText {
  readonly first: number;
  readonly last: number | null;
}

export type PageRangesResult =
  | { readonly ok: true; readonly ranges: readonly PageRangeText[] }
  | { readonly ok: false; readonly message: string };

const T = TEXT.pdfPress.ranges;
// `N`, `N-M` ou `N-`; a meia-risca vira hífen antes, porque teclados e corretores a trocam.
const PART = /^(\d+)(?:\s*(-)\s*(\d+)?)?$/;

/** Lê o texto do campo, na ordem e com repetições, ou devolve a mensagem da primeira parte ruim. */
export function parsePageRanges(text: string, maxPage: number): PageRangesResult {
  if (text.trim() === "") {
    return { ok: false, message: T.empty };
  }
  const ranges: PageRangeText[] = [];
  for (const raw of text.split(",")) {
    const part = raw.trim();
    const parsed = parsePart(part, maxPage);
    if (typeof parsed === "string") {
      return { ok: false, message: parsed };
    }
    ranges.push(parsed);
  }
  return { ok: true, ranges };
}

function parsePart(part: string, maxPage: number): PageRangeText | string {
  const match = PART.exec(part.replaceAll("–", "-"));
  if (match === null) {
    return T.invalidPart(part);
  }
  const [, firstText, dash, lastText] = match;
  const first = Number(firstText);
  const last = dash === undefined ? first : lastText === undefined ? null : Number(lastText);
  if (first < 1 || last === 0) {
    return T.startsAtOne;
  }
  const highest = Math.max(first, last ?? first);
  if (highest > maxPage) {
    return T.beyondLimit(highest, maxPage);
  }
  if (last !== null && last < first) {
    return T.reversed(part);
  }
  return { first, last };
}
