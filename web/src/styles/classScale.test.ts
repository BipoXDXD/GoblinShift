import { describe, expect, it } from "vitest";

/*
 * Só os valores do sistema (RUI cap. 1 e seção 7 do plano de design), conferidos no código-fonte:
 * o tokens.css já tira do Tailwind as cores, sombras, raios e pesos de fora, mas espaçamento
 * numérico e valor arbitrário continuam gerando CSS, e a CSP proíbe estilo inline.
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
