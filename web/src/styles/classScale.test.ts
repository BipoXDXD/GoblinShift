import { parse } from "culori";
import { describe, expect, it } from "vitest";

/*
 * Só os valores do sistema (RUI cap. 1 e seção 7 do plano de design), conferidos no código-fonte:
 * o tokens.css já tira do Tailwind as cores, sombras, raios e pesos de fora, mas espaçamento
 * numérico e valor arbitrário continuam gerando CSS, e a CSP proíbe estilo inline. As folhas de
 * estilo (`*.css`) passam pela régua equivalente, declaração por declaração (cssViolations, que
 * lista o que cobre).
 */

/** Escala de espaço: 4, 8, 12, 16, 24, 32, 48, 64, 96 e 128 px; 160 e 192 para arte e painéis. */
const SPACING_SCALE = new Set([
  "0",
  "1",
  "2",
  "3",
  "4",
  "6",
  "8",
  "12",
  "16",
  "24",
  "32",
  "40",
  "48",
]);
/**
 * Fora da escala por motivo listado: alvo de toque de 44 px, a coluna de parâmetros de 448 px e a
 * caixa 4:5 da goblin em destaque (192×240 no celular, 256×320 a partir de 768 px).
 */
const SPACING_EXCEPTIONS = new Set([
  "min-h-11",
  "min-w-11",
  "size-11",
  "w-112",
  "h-60",
  "h-80",
  "w-64",
]);

const SPACING_UTILITY =
  /(?<=^|[\s"'`:])-?((?:p|m)[xytrblse]?|gap(?:-[xy])?|space-[xy]|w|h|size|min-w|min-h|max-w|max-h|inset(?:-[xy])?|top|right|bottom|left|translate-[xy])-([0-9][0-9.]*)(?=$|[\s"'`])/g;
const ARBITRARY_VALUE = /([a-z0-9]+)-\[[^\]]*\](:?)/g;
const RAW_COLOR =
  /(?<=^|[\s"'`:])(?:bg|text|border|outline|ring|fill|stroke|shadow|accent|decoration|divide|caret)-(?:wood|copper|amber|ember|patina|white)(?:-[0-9]+)?\b/g;
/** Três níveis de elevação mais a rebaixada (spec 2026-10-15, decisão 2). */
const ALLOWED_SHADOWS = new Set(["card", "float", "dialog", "sunken", "none"]);
/** O nível vai até o fim da classe, para que opacidade (`/20`) e variável (`(--x)`) não escapem. */
const SHADOW_UTILITY = /(?<=^|[\s"'`:])shadow-([^\s"'`]+)/g;
/** Camadas com nome (CSSD 6.4.3); o <dialog> modal usa a top layer e não precisa de z-index. */
const Z_LAYERS = new Set(["--z-menu", "--z-skip"]);
const Z_UTILITY = /(?<=^|[\s"'`:])-?z-(\d+|\(([^)]*)\))(?=$|[\s"'`])/g;
const SCREEN_HEIGHT = /(?<=^|[\s"'`:])(?:min-|max-)?h-screen(?=$|[\s"'`])/g;

/** Problemas de estilo num arquivo-fonte, cada um com a classe ou o trecho que o causou. */
function styleViolations(source: string): string[] {
  const violations: string[] = [];
  for (const [utility, prefix, value] of source.matchAll(SPACING_UTILITY)) {
    const plain = utility.replace(/^-/, "");
    if (!SPACING_SCALE.has(value ?? "") && !SPACING_EXCEPTIONS.has(plain)) {
      violations.push(`espaçamento fora da escala: ${utility} (${prefix ?? ""})`);
    }
  }
  for (const [match, name, colon] of source.matchAll(ARBITRARY_VALUE)) {
    // `data-[...]:` e `aria-[...]:` são variantes que leem atributos, não valores soltos.
    const isAttributeVariant = (name === "data" || name === "aria") && colon === ":";
    if (!isAttributeVariant) {
      violations.push(`valor arbitrário: ${match}`);
    }
  }
  for (const [match] of source.matchAll(RAW_COLOR)) {
    violations.push(`tom cru em componente (use o papel semântico): ${match}`);
  }
  for (const [match, level] of source.matchAll(SHADOW_UTILITY)) {
    if (!ALLOWED_SHADOWS.has(level ?? "")) {
      violations.push(`sombra fora dos níveis: ${match}`);
    }
  }
  for (const [match, , variable] of source.matchAll(Z_UTILITY)) {
    if (variable === undefined || !Z_LAYERS.has(variable)) {
      violations.push(`z-index fora das camadas: ${match}`);
    }
  }
  for (const [match] of source.matchAll(SCREEN_HEIGHT)) {
    violations.push(`altura de janela em vh: ${match} (use svh)`);
  }
  if (/\boutline-none\b/.test(source)) {
    violations.push("outline-none: o foco visível vem do base.css e não pode sumir");
  }
  if (/\bstyle=\{/.test(source)) {
    violations.push("style inline: a CSP (style-src 'self') bloqueia; use classe");
  }
  return violations;
}

/** Problema numa folha de estilo: a regra violada e onde (`blocos › … { declaração }`). */
interface CssViolation {
  readonly rule: string;
  readonly where: string;
}

/** Texto entre dois delimitadores do CSS e o caminho dos blocos que o contêm ("" no topo). */
interface CssStatement {
  readonly scope: string;
  readonly text: string;
}

type CssDelimiter = "{" | "}" | ";" | "fim";

const COMMENT = /\/\*[\s\S]*?\*\//g;
const SCOPE_SEPARATOR = " › ";
const TOP_LEVEL = "(topo)";
const APPLY = "@apply ";
/** As únicas at-rules sem bloco que o topo das folhas usa hoje (Tailwind e importação). */
const TOP_LEVEL_AT_RULE = /^@(?:import|custom-variant) /;
const IMPORTANT = /\s*!important$/;

/** Espaço e tamanho só com `0`, `%`, palavra-chave, `var()` e `--spacing(n)` da escala. */
const SPACING_PROPERTY =
  /^(?:margin|padding|gap|row-gap|column-gap|width|height|min-width|min-height|max-width|max-height|inset|top|right|bottom|left|translate|(?:min-|max-)?(?:inline|block)-size)(?:-[a-z-]+)?$/;
/**
 * Comprimento com unidade, negativo inclusive. `svh`, `dvh` e `lvh` ficam de fora: são a altura de
 * janela certa, a mesma de `min-h-svh`.
 */
const LITERAL_LENGTH = /(?<![\w.])-?\d*\.?\d+(?:px|rem|em|ch|ex|vw|vh)\b/;
const SPACING_FUNCTION = /--spacing\(([^)]*)\)/g;
const RAW_CSS_COLOR = /var\(--color-[a-z]+-\d+\)|\boklch\(|#[0-9a-f]{3,8}\b|\brgba?\(|\bhsla?\(/i;
/** Propriedades que recebem cor, onde um nome de cor (`white`, `navy`…) é tom cru. */
const COLOR_PROPERTY =
  /(?:^|-)color$|^background$|^border(?:-(?:top|right|bottom|left|block|inline)(?:-(?:start|end))?)?$|^outline$|^(?:fill|stroke)$|shadow$|^text-decoration$/;
/** Palavra solta do valor: fora ficam `--token`, unidade colada ao número e trecho de palavra. */
const CSS_WORD = /(?<![\w-])[a-z][a-z-]*(?![\w-])/gi;
/** Nomes que o culori lê como cor, mas que não são tom cru: não pintam com cor própria. */
const NOT_RAW_COLOR = new Set(["transparent", "currentcolor"]);
const ELEVATION = /^var\(--elevation-([a-z]+)\)$/;
const Z_LAYER = /^var\((--z-[a-z]+)\)$/;
const VIEWPORT_HEIGHT = /\dvh\b/;
/** Valores que apagam o anel de foco, por propriedade. */
const HIDDEN_OUTLINE = new Map([
  ["outline", new Set(["none", "0"])],
  ["outline-style", new Set(["none", "hidden"])],
  ["outline-width", new Set(["0", "0px"])],
]);
const RADIUS = String.raw`(?:0|var\(--radius-(?:lg|full)\))`;
/** Tipo e raio só pelos tokens: escala de tipo, dois pesos (RUI cap. 2) e um raio (D10). */
const SYSTEM_VALUES: readonly (readonly [RegExp, RegExp, string])[] = [
  [/^font-size$/, /^(?:inherit|var\(--text-[a-z0-9]+\))$/, "tamanho de fonte fora da escala"],
  [
    /^font-weight$/,
    /^(?:inherit|normal|400|600|var\(--font-weight-(?:normal|semibold)\))$/,
    "peso fora dos dois do sistema",
  ],
  [
    /^line-height$/,
    /^(?:inherit|normal|var\(--text-[a-z0-9]+--line-height\))$/,
    "altura de linha fora da escala de tipo",
  ],
  [/^font$/, /^(?:inherit|var\(--[a-z0-9-]+\))$/, "font abreviada fora do sistema"],
  [/^border(?:-[a-z]+)*-radius$/, new RegExp(`^${RADIUS}(?: ${RADIUS})*$`), "raio fora do sistema"],
];

/**
 * A régua das classes no CSS, declaração por declaração: espaço e tamanho (inclusive negativo e
 * lógico), cor crua (token cru, função de cor ou nome de cor), sombra, z-index, vh, anel de foco
 * apagado (`outline`, `outline-style`, `outline-width`, com ou sem `!important`), tipo (tamanho,
 * peso, altura de linha, `font`), raio e `@apply`. Custom properties (`--x: …`) ficam de fora: são
 * as definições dos tokens, medidas em contrast.test.ts. O que a leitura não reconhece (at-rule
 * desconhecida, declaração no topo ou sem `:`) reprova, e chave sem par lança erro.
 */
function cssViolations(source: string): CssViolation[] {
  return cssStatements(source).flatMap((statement) => {
    const where = `${statement.scope === "" ? TOP_LEVEL : statement.scope} { ${statement.text} }`;
    return statementViolations(statement).map((rule) => ({ rule, where }));
  });
}

/**
 * Lê o CSS contando chaves: toda declaração sai com o caminho dos blocos que a contêm, em qualquer
 * nível (aninhamento, `@media` dentro de regra, texto depois de um bloco filho). Aspas e
 * parênteses protegem `;` e chaves dentro de strings e `url()`.
 */
function cssStatements(source: string): CssStatement[] {
  const statements: CssStatement[] = [];
  const scopes: string[] = [];
  for (const [delimiter, raw] of splitAtDelimiters(source.replace(COMMENT, ""))) {
    const text = collapseSpaces(raw);
    if (delimiter === "{") {
      scopes.push(text);
      continue;
    }
    if (text !== "") {
      statements.push({ scope: scopes.join(SCOPE_SEPARATOR), text });
    }
    if (delimiter === "}" && scopes.pop() === undefined) {
      throw new Error("CSS com `}` sem `{` correspondente");
    }
  }
  if (scopes.length > 0) {
    throw new Error(`CSS com \`{\` sem \`}\`: ${scopes.join(SCOPE_SEPARATOR)}`);
  }
  return statements;
}

function splitAtDelimiters(css: string): [CssDelimiter, string][] {
  const pieces: [CssDelimiter, string][] = [];
  let text = "";
  let quote: string | null = null;
  let parens = 0;
  for (const char of css) {
    const delimiter = quote === null && parens === 0 ? delimiterOf(char) : null;
    if (delimiter !== null) {
      pieces.push([delimiter, text]);
      text = "";
      continue;
    }
    text += char;
    if (quote !== null) {
      quote = char === quote ? null : quote;
    } else if (char === '"' || char === "'") {
      quote = char;
    } else if (char === "(") {
      parens += 1;
    } else if (char === ")") {
      parens -= 1;
    }
  }
  if (quote !== null || parens !== 0) {
    throw new Error("CSS com aspas ou parênteses sem par");
  }
  pieces.push(["fim", text]);
  return pieces;
}

function delimiterOf(char: string): CssDelimiter | null {
  return char === "{" || char === "}" || char === ";" ? char : null;
}

function collapseSpaces(text: string): string {
  return text.replace(/\s+/g, " ").trim();
}

function statementViolations({ scope, text }: CssStatement): string[] {
  if (text.startsWith("@")) {
    return atRuleViolations(scope, text);
  }
  if (scope === "") {
    return [`declaração fora de regra: ${text}`];
  }
  const colon = text.indexOf(":");
  if (colon === -1) {
    return [`declaração ilegível: ${text}`];
  }
  const property = text.slice(0, colon).trim();
  if (property.startsWith("--")) {
    return [];
  }
  const value = text
    .slice(colon + 1)
    .replace(IMPORTANT, "")
    .trim();
  return [
    ...spacingViolations(property, value),
    ...colorViolations(property, value),
    ...(property === "box-shadow" && !isElevation(value)
      ? [`sombra fora dos níveis: ${value}`]
      : []),
    ...(property === "z-index" && !isZLayer(value) ? [`z-index fora das camadas: ${value}`] : []),
    ...(VIEWPORT_HEIGHT.test(value) ? [`altura de janela em vh: ${value} (use svh)`] : []),
    ...(hidesOutline(property, value) ? ["outline: none: o foco visível não pode sumir"] : []),
    ...SYSTEM_VALUES.filter(([name, allowed]) => name.test(property) && !allowed.test(value)).map(
      ([, , rule]) => `${rule}: ${property}: ${value}`,
    ),
  ];
}

function atRuleViolations(scope: string, text: string): string[] {
  if (text.startsWith(APPLY)) {
    return styleViolations(text.slice(APPLY.length));
  }
  if (scope === "" && TOP_LEVEL_AT_RULE.test(text)) {
    return [];
  }
  return [`at-rule fora da régua: ${text}`];
}

function spacingViolations(property: string, value: string): string[] {
  if (!SPACING_PROPERTY.test(property)) {
    return [];
  }
  const offScale = [...value.matchAll(SPACING_FUNCTION)]
    .filter(([, step]) => !SPACING_SCALE.has(step?.trim() ?? ""))
    .map(([call]) => `espaçamento fora da escala em ${property}: ${call}`);
  return LITERAL_LENGTH.test(value)
    ? [`comprimento solto em ${property}: ${value} (use --spacing())`, ...offScale]
    : offScale;
}

function colorViolations(property: string, value: string): string[] {
  const named = COLOR_PROPERTY.test(property) ? namedColors(value) : [];
  if (named.length > 0) {
    return [`cor crua em ${property} (use o papel semântico): ${named.join(", ")}`];
  }
  return RAW_CSS_COLOR.test(value) ? [`cor crua em ${property} (use o papel semântico)`] : [];
}

/** Qualquer nome de cor CSS (os 148 que o culori conhece), não uma lista escrita à mão. */
function namedColors(value: string): string[] {
  return [...value.matchAll(CSS_WORD)]
    .map(([word]) => word)
    .filter((word) => !NOT_RAW_COLOR.has(word.toLowerCase()) && parse(word) !== undefined);
}

/** O anel some se qualquer parte do valor o apaga (`outline: 0 none`, `none 0`). */
function hidesOutline(property: string, value: string): boolean {
  const hidden = HIDDEN_OUTLINE.get(property);
  return hidden !== undefined && value.split(" ").some((part) => hidden.has(part));
}

function isElevation(value: string): boolean {
  const level = ELEVATION.exec(value)?.[1];
  return value === "none" || (level !== undefined && ALLOWED_SHADOWS.has(level));
}

function isZLayer(value: string): boolean {
  const layer = Z_LAYER.exec(value)?.[1];
  return layer !== undefined && Z_LAYERS.has(layer);
}

describe("styleViolations", () => {
  it.each([
    ["p-5", "espaçamento fora da escala: p-5"],
    ["mt-0.5", "espaçamento fora da escala: mt-0.5"],
    ["lg:gap-7", "espaçamento fora da escala: gap-7"],
    ["-mt-10", "espaçamento fora da escala: -mt-10"],
    ["w-[13px]", "valor arbitrário: w-[13px]"],
    ["grid-cols-[1fr_2fr]", "valor arbitrário: cols-[1fr_2fr]"],
    ["bg-copper-700", "tom cru em componente (use o papel semântico): bg-copper-700"],
    ["text-wood-900", "tom cru em componente (use o papel semântico): text-wood-900"],
    ["ring-amber-400", "tom cru em componente (use o papel semântico): ring-amber-400"],
    ["text-patina-800", "tom cru em componente (use o papel semântico): text-patina-800"],
    ["shadow-3", "sombra fora dos níveis: shadow-3"],
    ["hover:shadow-md", "sombra fora dos níveis: shadow-md"],
    ["shadow-lg/20", "sombra fora dos níveis: shadow-lg/20"],
    ["shadow-(--x)", "sombra fora dos níveis: shadow-(--x)"],
    ["z-10", "z-index fora das camadas: z-10"],
    ["focus:z-(--qualquer)", "z-index fora das camadas: z-(--qualquer)"],
    ["min-h-screen", "altura de janela em vh: min-h-screen"],
    ["focus:outline-none", "outline-none"],
    ["<div style={{ width: 1 }} />", "style inline"],
  ])("recusa %s", (source, expected) => {
    const found = styleViolations(`className="${source}"`).join("\n");
    expect(found).toContain(expected);
  });

  it.each([
    "p-4 gap-6 mt-12 -mt-8",
    "min-h-11 min-w-11 lg:w-112",
    "max-w-3xl w-full h-auto",
    "data-[dragging=true]:shadow-float",
    "shadow-card hover:shadow-float shadow-dialog shadow-sunken active:shadow-none",
    "z-(--z-menu) focus:z-(--z-skip) min-h-svh",
    "bg-surface text-fg-muted border-line",
    "arquivo-001 csv-janitor-parametros",
  ])("aceita %s", (source) => {
    expect(styleViolations(`className="${source}"`)).toEqual([]);
  });
});

describe("cssViolations", () => {
  it.each([
    ["a { padding: 13px; }", "comprimento solto em padding: 13px"],
    ["a { margin-top: 0.5rem; }", "comprimento solto em margin-top: 0.5rem"],
    ["a { width: --spacing(5); }", "espaçamento fora da escala em width: --spacing(5)"],
    ["a { color: var(--color-copper-700); }", "cor crua em color"],
    ["a { background-color: oklch(0.5 0.1 50); }", "cor crua em background-color"],
    ["a { border-color: #fff; }", "cor crua em border-color"],
    ["a { box-shadow: 0 1px 2px black; }", "sombra fora dos níveis"],
    ["a { box-shadow: var(--elevation-huge); }", "sombra fora dos níveis"],
    ["a { z-index: 10; }", "z-index fora das camadas"],
    ["a { min-height: 100vh; }", "altura de janela em vh"],
    ["a:focus { outline: none; }", "outline: none"],
    ["a:focus { outline: 0; }", "outline: none"],
    ["a { @apply p-5; }", "espaçamento fora da escala: p-5"],
    ["a { margin: -13px; }", "comprimento solto em margin: -13px"],
    ["a { inset: 0 -6px; }", "comprimento solto em inset: 0 -6px"],
    ["a { inline-size: 13px; }", "comprimento solto em inline-size: 13px"],
    ["a { max-block-size: 7rem; }", "comprimento solto em max-block-size: 7rem"],
    // Aninhamento: o que vem antes e depois do bloco filho continua na régua.
    ["a { padding: 13px; &:hover { color: var(--fg); } margin: 7px; }", "em padding: 13px"],
    ["a { padding: 13px; &:hover { color: var(--fg); } margin: 7px; }", "em margin: 7px"],
    ["a { @media (width >= 48rem) { padding: 13px; } }", "em padding: 13px"],
    ["a { &:hover { outline: none; } }", "outline: none"],
    ["a { color: white; }", "cor crua em color"],
    ["a { background: black; }", "cor crua em background"],
    ["a { border-radius: 7px; }", "raio fora do sistema: border-radius: 7px"],
    ["a { border-top-left-radius: 3px; }", "raio fora do sistema"],
    ["a { font-size: 13px; }", "tamanho de fonte fora da escala: font-size: 13px"],
    ["a { font-weight: 300; }", "peso fora dos dois do sistema: font-weight: 300"],
    ["a { line-height: 18px; }", "altura de linha fora da escala de tipo: line-height: 18px"],
    ["a { font: 13px/1 serif; }", "font abreviada fora do sistema"],
    ["a:focus { outline: none !important; }", "outline: none"],
    ["a:focus { outline-style: none; }", "outline: none"],
    ["a:focus { outline-width: 0; }", "outline: none"],
    ["a:focus { outline: 0 none; }", "outline: none"],
    ["a:focus { outline: none 0; }", "outline: none"],
    ["a { color: navy; }", "cor crua em color (use o papel semântico): navy"],
    ["a { background-color: teal; }", "cor crua em background-color (use o papel semântico): teal"],
    ["a { border: 2px solid crimson; }", "cor crua em border (use o papel semântico): crimson"],
    ["a { outline: 3px solid rebeccapurple; }", "cor crua em outline (use o papel semântico)"],
    ["padding: 13px;", "declaração fora de regra: padding: 13px"],
    ["a { @include x; }", "at-rule fora da régua: @include x"],
    ["@plugin x;", "at-rule fora da régua: @plugin x"],
    ["a { padding }", "declaração ilegível: padding"],
  ])("recusa %s", (source, expected) => {
    const found = cssViolations(source)
      .map(({ rule }) => rule)
      .join("\n");
    expect(found).toContain(expected);
  });

  it.each([
    "a { padding: --spacing(2) --spacing(3); margin: 0; width: 100%; inset: 0; }",
    "a { min-height: 100svh; height: --spacing(24); }",
    "a { color: var(--fg); background: color-mix(in srgb, var(--overlay) 60%, transparent); }",
    "a { box-shadow: var(--elevation-sunken); } b { box-shadow: none; }",
    "a { z-index: var(--z-menu); }",
    "a:focus-visible { outline: 3px solid var(--ring); outline-offset: 2px; }",
    ":root { --surface: var(--color-wood-50); --z-skip: 20; --x: oklch(1 0 0); }",
    "/* padding: 13px; outline: none */ a { color: var(--link); }",
    "a { border-radius: var(--radius-lg); font: inherit; font-weight: 600; font-size: var(--text-sm); }",
    "a { border-radius: var(--radius-full) 0; line-height: var(--text-sm--line-height); }",
    'a { content: "{;}"; background-image: url("a;b.png"); white-space: nowrap; }',
    "a { color: currentcolor; background-color: transparent; border: 2px solid var(--border-ui); }",
    "a { border-color: currentColor; outline: 3px dashed var(--ring); text-decoration: underline; }",
    "a { animation-duration: 0.01ms !important; inset-inline: 0; margin-block: --spacing(4); }",
    '@import "tailwindcss"; @custom-variant dark (&:where([data-theme="dark"], [data-theme="dark"] *));',
  ])("aceita %s", (source) => {
    expect(cssViolations(source)).toEqual([]);
  });

  it("aponta os blocos e a declaração do problema", () => {
    expect(cssViolations("@layer base {\n  a,\n  b:focus {\n    outline: none;\n  }\n}")).toEqual([
      {
        rule: "outline: none: o foco visível não pode sumir",
        where: "@layer base › a, b:focus { outline: none }",
      },
    ]);
  });

  it.each(["a { padding: --spacing(2);", "a { color: var(--fg); } }", 'a { content: "x; }'])(
    "recusa CSS que não fecha: %s",
    (source) => {
      expect(() => cssViolations(source)).toThrow(/sem/);
    },
  );
});

/*
 * Desvios de propósito no CSS, cada um com o motivo. A chave é `arquivo › blocos { declaração }`,
 * exatamente como cssViolations aponta; exceção que deixou de ser usada reprova (abaixo).
 */
const CSS_EXCEPTIONS = new Map([
  [
    "base.css › @layer base › :focus:not(:focus-visible) { outline: none }",
    "clique de mouse não desenha anel; o teclado continua com o :focus-visible",
  ],
  [
    'base.css › @layer base › h1[tabindex="-1"]:focus { outline: none }',
    "o título recebe foco por programa a cada rota (FocusHeading) e não é controle",
  ],
  [
    "base.css › @layer components › .card-link:focus-visible { outline: none }",
    "o anel passa do link para o cartão inteiro, em .card-link:focus-visible::after",
  ],
  [
    'base.css › @layer base › input:where(:not([type="checkbox"], [type="radio"], [type="file"], [type="range"])), select, textarea { min-height: --spacing(11) }',
    "alvo de toque de 44 px, o mesmo motivo de min-h-11 nas classes",
  ],
  // Ruling da triagem (item 18): a caixa fica com 20 px porque o alvo de toque é a linha e o
  // label, com min-h-11 (44 px, Field layout="inline" e ThemeSwitcher), acima dos 24 px da WCAG
  // 2.5.8 AA; o e2e controls.spec.ts mede a altura dos labels.
  [
    'base.css › @layer base › input[type="checkbox"], input[type="radio"] { width: --spacing(5) }',
    "caixa de 20 px; o alvo de toque é o label de 44 px",
  ],
  [
    'base.css › @layer base › input[type="checkbox"], input[type="radio"] { height: --spacing(5) }',
    "caixa de 20 px; o alvo de toque é o label de 44 px",
  ],
  [
    "fonts.css › @font-face { font-weight: 200 800 }",
    "descritor do @font-face: a faixa de pesos que o arquivo variável oferece, não um peso aplicado",
  ],
]);

const CSS_SOURCES = import.meta.glob<string>("../**/*.css", {
  query: "?raw",
  import: "default",
  eager: true,
});

function cssFileName(path: string): string {
  return path.slice(path.lastIndexOf("/") + 1);
}

describe("folhas de estilo", () => {
  it("tem os quatro arquivos para conferir", () => {
    expect(Object.keys(CSS_SOURCES).map(cssFileName).sort()).toEqual([
      "base.css",
      "fonts.css",
      "index.css",
      "tokens.css",
    ]);
  });

  it.each(Object.entries(CSS_SOURCES))("%s usa só valores do sistema", (path, source) => {
    const unexpected = cssViolations(source)
      .map(({ rule, where }) => ({ rule, key: `${cssFileName(path)} › ${where}` }))
      .filter(({ key }) => !CSS_EXCEPTIONS.has(key));
    expect(unexpected).toEqual([]);
  });

  it("toda exceção ainda aponta para um desvio que existe", () => {
    const found = new Set(
      Object.entries(CSS_SOURCES).flatMap(([path, source]) =>
        cssViolations(source).map(({ where }) => `${cssFileName(path)} › ${where}`),
      ),
    );
    expect([...CSS_EXCEPTIONS.keys()].filter((key) => !found.has(key))).toEqual([]);
  });
});

const SOURCES = import.meta.glob<string>(
  ["../**/*.{ts,tsx}", "!../**/*.test.{ts,tsx}", "!../test/**"],
  {
    query: "?raw",
    import: "default",
    eager: true,
  },
);

describe("código-fonte da interface", () => {
  it("tem arquivos para conferir", () => {
    expect(Object.keys(SOURCES).length).toBeGreaterThan(10);
  });

  it.each(Object.entries(SOURCES))("%s usa só valores do sistema", (_path, source) => {
    expect(styleViolations(source)).toEqual([]);
  });
});
