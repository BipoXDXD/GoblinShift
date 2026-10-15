// Markdown dos textos de docs/legal/ → blocos tipados, que o LegalDocument desenha como elementos
// React. Cobre só o subconjunto que esses textos usam (títulos, parágrafos, citação, listas,
// tabelas, régua, negrito, código e links), sem HTML: nada vira innerHTML, então um `<` no texto
// é só texto. Sintaxe fora do subconjunto sai como texto literal, nunca some.

export type Inline =
  | { readonly kind: "text"; readonly text: string }
  | { readonly kind: "strong"; readonly children: readonly Inline[] }
  | { readonly kind: "code"; readonly text: string }
  | { readonly kind: "link"; readonly href: string; readonly children: readonly Inline[] };

export type Block =
  | { readonly kind: "heading"; readonly level: 1 | 2 | 3 | 4; readonly inlines: readonly Inline[] }
  | { readonly kind: "paragraph"; readonly inlines: readonly Inline[] }
  | { readonly kind: "quote"; readonly blocks: readonly Block[] }
  | {
      readonly kind: "list";
      readonly ordered: boolean;
      readonly items: readonly (readonly Inline[])[];
    }
  | {
      readonly kind: "table";
      readonly header: readonly (readonly Inline[])[];
      readonly rows: readonly (readonly (readonly Inline[])[])[];
    }
  | { readonly kind: "rule" };

const HEADING = /^(#{1,4}) +(.*)$/;
const BULLET = /^- +(.*)$/;
const NUMBERED = /^\d+\. +(.*)$/;
const RULE = /^-{3,}$/;
const TABLE_SEPARATOR = /^\|(?: *:?-+:? *\|)+$/;
const CONTINUATION = /^ {2,}\S/;

/** Blocos do documento inteiro, na ordem. */
export function parseMarkdown(source: string): Block[] {
  return parseLines(source.replace(/\r\n?/g, "\n").split("\n"));
}

function parseLines(lines: readonly string[]): Block[] {
  const blocks: Block[] = [];
  let index = 0;
  while (index < lines.length) {
    const line = lines[index] ?? "";
    if (line.trim() === "") {
      index += 1;
      continue;
    }
    const parsed = parseBlock(lines, index);
    blocks.push(parsed.block);
    index = parsed.next;
  }
  return blocks;
}

interface Parsed {
  readonly block: Block;
  readonly next: number;
}

function parseBlock(lines: readonly string[], start: number): Parsed {
  const line = lines[start] ?? "";
  const heading = HEADING.exec(line);
  if (heading !== null) {
    const level = (heading[1] ?? "#").length;
    return {
      block: {
        kind: "heading",
        level: headingLevel(level),
        inlines: parseInline(heading[2] ?? ""),
      },
      next: start + 1,
    };
  }
  if (RULE.test(line)) {
    return { block: { kind: "rule" }, next: start + 1 };
  }
  if (line.startsWith(">")) {
    return parseQuote(lines, start);
  }
  if (line.startsWith("|") && TABLE_SEPARATOR.test(lines[start + 1] ?? "")) {
    return parseTable(lines, start);
  }
  if (BULLET.test(line) || NUMBERED.test(line)) {
    return parseList(lines, start);
  }
  return parseParagraph(lines, start);
}

function headingLevel(hashes: number): 1 | 2 | 3 | 4 {
  switch (hashes) {
    case 1:
      return 1;
    case 2:
      return 2;
    case 3:
      return 3;
    default:
      return 4;
  }
}

function parseQuote(lines: readonly string[], start: number): Parsed {
  const inner: string[] = [];
  let index = start;
  while (index < lines.length && (lines[index] ?? "").startsWith(">")) {
    inner.push((lines[index] ?? "").replace(/^> ?/, ""));
    index += 1;
  }
  return { block: { kind: "quote", blocks: parseLines(inner) }, next: index };
}

function parseTable(lines: readonly string[], start: number): Parsed {
  const header = splitRow(lines[start] ?? "");
  const rows: Inline[][][] = [];
  let index = start + 2;
  while (index < lines.length && (lines[index] ?? "").startsWith("|")) {
    rows.push(splitRow(lines[index] ?? ""));
    index += 1;
  }
  return { block: { kind: "table", header, rows }, next: index };
}

function splitRow(line: string): Inline[][] {
  return line
    .trim()
    .replace(/^\||\|$/g, "")
    .split("|")
    .map((cell) => parseInline(cell.trim()));
}

/**
 * Itens de uma lista. Linha recuada continua o item anterior; linha em branco seguida de outro
 * item do mesmo tipo continua a lista (lista "solta").
 */
function parseList(lines: readonly string[], start: number): Parsed {
  const ordered = NUMBERED.test(lines[start] ?? "");
  const marker = ordered ? NUMBERED : BULLET;
  const items: string[] = [];
  let index = start;
  while (index < lines.length) {
    const line = lines[index] ?? "";
    const item = marker.exec(line);
    if (item !== null) {
      items.push(item[1] ?? "");
    } else if (CONTINUATION.test(line) && items.length > 0) {
      items[items.length - 1] = `${items.at(-1) ?? ""} ${line.trim()}`;
    } else if (line.trim() === "" && marker.test(lines[index + 1] ?? "")) {
      // Linha em branco entre dois itens: a lista segue.
    } else {
      break;
    }
    index += 1;
  }
  return {
    block: { kind: "list", ordered, items: items.map((item) => parseInline(item)) },
    next: index,
  };
}

function parseParagraph(lines: readonly string[], start: number): Parsed {
  const text: string[] = [];
  let index = start;
  while (index < lines.length) {
    const line = lines[index] ?? "";
    if (line.trim() === "" || (index > start && startsBlock(lines, index))) {
      break;
    }
    text.push(line.trim());
    index += 1;
  }
  return { block: { kind: "paragraph", inlines: parseInline(text.join(" ")) }, next: index };
}

function startsBlock(lines: readonly string[], index: number): boolean {
  const line = lines[index] ?? "";
  return (
    HEADING.test(line) ||
    RULE.test(line) ||
    line.startsWith(">") ||
    BULLET.test(line) ||
    NUMBERED.test(line) ||
    (line.startsWith("|") && TABLE_SEPARATOR.test(lines[index + 1] ?? ""))
  );
}

// Negrito, código e link, nessa prioridade pela posição: o que começa antes ganha.
const INLINE_TOKEN = /\*\*(.+?)\*\*|`([^`]+)`|\[([^\]]+)\]\(([^)\s]+)\)/g;

/** Trechos de uma linha; negrito e link podem ter código e link dentro. */
export function parseInline(text: string): Inline[] {
  const inlines: Inline[] = [];
  let last = 0;
  for (const match of text.matchAll(INLINE_TOKEN)) {
    const [whole, strong, code, linkText, href] = match;
    if (match.index > last) {
      inlines.push({ kind: "text", text: text.slice(last, match.index) });
    }
    if (strong !== undefined) {
      inlines.push({ kind: "strong", children: parseInline(strong) });
    } else if (code !== undefined) {
      inlines.push({ kind: "code", text: code });
    } else if (linkText !== undefined && href !== undefined) {
      inlines.push({ kind: "link", href, children: parseInline(linkText) });
    }
    last = match.index + whole.length;
  }
  if (last < text.length) {
    inlines.push({ kind: "text", text: text.slice(last) });
  }
  return inlines;
}
