import { describe, expect, it } from "vitest";

/*
 * Só os valores do sistema (RUI cap. 1 e seção 7 do plano de design), conferidos no código-fonte:
 * o tokens.css já tira do Tailwind as cores, sombras, raios e pesos de fora, mas espaçamento
 * numérico e valor arbitrário continuam gerando CSS, e a CSP proíbe estilo inline. As folhas de
 * estilo (`*.css`) passam pela mesma régua, declaração por declaração (cssViolations).
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

/** Problema numa folha de estilo: a regra violada e onde (`seletor { propriedade: valor }`). */
interface CssViolation {
  readonly rule: string;
  readonly where: string;
}

interface CssDeclaration {
  readonly selector: string;
  readonly property: string;
  readonly value: string;
}

/** Espaço e tamanho só com `0`, `%`, palavra-chave, `var()` e `--spacing(n)` da escala. */
const SPACING_PROPERTY =
  /^(?:margin|padding|gap|row-gap|column-gap|width|height|min-width|min-height|max-width|max-height|inset|top|right|bottom|left)(?:-[a-z-]+)?$/;
/** `svh`, `dvh` e `lvh` ficam de fora: são a altura de janela certa, a mesma de `min-h-svh`. */
const LITERAL_LENGTH = /(?<![\w-])\d*\.?\d+(?:px|rem|em|ch|ex|vw|vh)\b/;
const SPACING_FUNCTION = /--spacing\(([^)]*)\)/g;
const RAW_CSS_COLOR = /var\(--color-[a-z]+-\d+\)|\boklch\(|#[0-9a-f]{3,8}\b|\brgba?\(|\bhsla?\(/i;
const ELEVATION = /^var\(--elevation-([a-z]+)\)$/;
const Z_LAYER = /^var\((--z-[a-z]+)\)$/;
const VIEWPORT_HEIGHT = /\dvh\b/;
const COMMENT = /\/\*[\s\S]*?\*\//g;
/** Bloco sem chaves dentro: a regra de estilo, mesmo aninhada em @layer ou @media. */
const INNERMOST_BLOCK = /([^{}]*)\{([^{}]*)\}/g;
const APPLY = "@apply ";

/**
 * A mesma régua das classes, aplicada às declarações das folhas de estilo. Custom properties
 * (`--x: …`) ficam de fora: são as definições dos tokens, medidas em contrast.test.ts.
 */
function cssViolations(source: string): CssViolation[] {
  return cssDeclarations(source).flatMap((declaration) => {
    const where = `${declaration.selector} { ${declaration.property}: ${declaration.value} }`;
    return declarationViolations(declaration).map((rule) => ({ rule, where }));
  });
}

function cssDeclarations(source: string): CssDeclaration[] {
  const css = source.replace(COMMENT, "");
  return [...css.matchAll(INNERMOST_BLOCK)].flatMap(([, prelude = "", body = ""]) => {
    // Antes do seletor pode vir texto de topo terminado em `;` (@import, @custom-variant).
    const selector = collapseSpaces(prelude.slice(prelude.lastIndexOf(";") + 1));
    return body.split(";").flatMap((text): CssDeclaration[] => {
      const declaration = collapseSpaces(text);
      if (declaration.startsWith(APPLY)) {
        return [{ selector, property: "@apply", value: declaration.slice(APPLY.length) }];
      }
      const colon = declaration.indexOf(":");
      if (colon === -1) {
        return [];
      }
      return [
        {
          selector,
          property: declaration.slice(0, colon).trim(),
          value: declaration.slice(colon + 1).trim(),
        },
      ];
    });
  });
}

function collapseSpaces(text: string): string {
  return text.replace(/\s+/g, " ").trim();
}

function declarationViolations({ property, value }: CssDeclaration): string[] {
  if (property === "@apply") {
    return styleViolations(value);
  }
  if (property.startsWith("--")) {
    return [];
  }
  return [
    ...spacingViolations(property, value),
    ...(RAW_CSS_COLOR.test(value) ? [`cor crua em ${property} (use o papel semântico)`] : []),
    ...(property === "box-shadow" && !isElevation(value)
      ? [`sombra fora dos níveis: ${value}`]
      : []),
    ...(property === "z-index" && !isZLayer(value) ? [`z-index fora das camadas: ${value}`] : []),
    ...(VIEWPORT_HEIGHT.test(value) ? [`altura de janela em vh: ${value} (use svh)`] : []),
    ...(property === "outline" && (value === "none" || value === "0")
      ? ["outline: none: o foco visível não pode sumir"]
      : []),
  ];
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
  ])("aceita %s", (source) => {
    expect(cssViolations(source)).toEqual([]);
  });

  it("aponta o seletor e a declaração do problema", () => {
    expect(cssViolations("@layer base {\n  a,\n  b:focus {\n    outline: none;\n  }\n}")).toEqual([
      {
        rule: "outline: none: o foco visível não pode sumir",
        where: "a, b:focus { outline: none }",
      },
    ]);
  });
});

/*
 * Desvios de propósito no CSS, cada um com o motivo. A chave é `arquivo › seletor { declaração }`,
 * exatamente como cssViolations aponta; exceção que deixou de ser usada reprova (abaixo).
 */
const CSS_EXCEPTIONS = new Map([
  [
    "base.css › :focus:not(:focus-visible) { outline: none }",
    "clique de mouse não desenha anel; o teclado continua com o :focus-visible",
  ],
  [
    'base.css › h1[tabindex="-1"]:focus { outline: none }',
    "o título recebe foco por programa a cada rota (FocusHeading) e não é controle",
  ],
  [
    "base.css › .card-link:focus-visible { outline: none }",
    "o anel passa do link para o cartão inteiro, em .card-link:focus-visible::after",
  ],
  [
    'base.css › input:where(:not([type="checkbox"], [type="radio"], [type="file"], [type="range"])), select, textarea { min-height: --spacing(11) }',
    "alvo de toque de 44 px, o mesmo motivo de min-h-11 nas classes",
  ],
  // Ruling da triagem (item 18): a caixa fica com 20 px porque o alvo de toque é a linha e o
  // label, com min-h-11 (44 px, Field layout="inline" e ThemeSwitcher), acima dos 24 px da WCAG
  // 2.5.8 AA; o e2e controls.spec.ts mede a altura dos labels.
  [
    'base.css › input[type="checkbox"], input[type="radio"] { width: --spacing(5) }',
    "caixa de 20 px; o alvo de toque é o label de 44 px",
  ],
  [
    'base.css › input[type="checkbox"], input[type="radio"] { height: --spacing(5) }',
    "caixa de 20 px; o alvo de toque é o label de 44 px",
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
