import { displayable, parse, wcagContrast } from "culori";
import { describe, expect, it } from "vitest";

import { rawColors, readTokensCss, semanticColors } from "../test/cssTokens";

// WCAG 2.2: 1.4.3 (texto) e 1.4.11 (componentes de UI, foco, texto grande).
const TEXT = 4.5;
const NON_TEXT = 3;

interface ContrastPair {
  readonly foreground: string;
  readonly background: string;
  readonly minimum: number;
}

function pairs(foreground: string, backgrounds: readonly string[], minimum: number) {
  return backgrounds.map((background): ContrastPair => ({ foreground, background, minimum }));
}

const SURFACES = ["bg", "surface", "surface-raised", "surface-sunken"] as const;

/** Todo par de cor que aparece junto na interface, com o mínimo que a WCAG pede para ele. */
const CONTRAST_PAIRS: readonly ContrastPair[] = [
  ...pairs("fg", SURFACES, TEXT),
  ...pairs("fg-muted", SURFACES, TEXT),
  ...pairs("link", SURFACES, TEXT),
  ...pairs("secondary-fg", ["bg", "surface", "surface-raised"], TEXT),
  ...pairs("on-primary", ["primary", "primary-hover"], TEXT),
  ...pairs("on-destructive", ["destructive", "destructive-hover"], TEXT),
  ...pairs("success-fg", ["success-bg", "surface"], TEXT),
  ...pairs("warning-fg", ["warning-bg"], TEXT),
  ...pairs("danger-fg", ["danger-bg", "bg", "surface", "surface-sunken"], TEXT),
  ...pairs("info-fg", ["info-bg"], TEXT),
  ...pairs("fg", ["dropzone-active"], TEXT),
  ...pairs("fg-muted", ["dropzone-active"], TEXT),
  ...pairs("selection-fg", ["selection-bg"], TEXT),
  // Terciário: só texto grande (18 px ou mais) ou decorativo.
  ...pairs("fg-subtle", ["bg", "surface"], NON_TEXT),
  ...pairs("border-ui", [...SURFACES, "dropzone-active"], NON_TEXT),
  ...pairs("ring", [...SURFACES, "danger-bg"], NON_TEXT),
  ...pairs("progress-fill", ["progress-track"], NON_TEXT),
];

const THEMES = [
  { name: "claro", selector: ":root" },
  { name: "escuro", selector: '[data-theme="dark"]' },
] as const;

const css = readTokensCss();

/** success-bg, success-fg e progress-fill. */
const STATE_ROLE_COUNT = 3;

describe.each(THEMES)("tema $name", ({ name, selector }) => {
  const colors = semanticColors(css, selector);

  it.each(CONTRAST_PAIRS)(
    "$foreground × $background ≥ $minimum",
    ({ foreground, background, minimum }) => {
      const ratio = wcagContrast(colorOf(colors, foreground), colorOf(colors, background));
      expect(
        ratio,
        `${name}: ${foreground} × ${background} = ${ratio.toFixed(2)} < ${String(minimum)}`,
      ).toBeGreaterThanOrEqual(minimum);
    },
  );
});

describe("tons crus", () => {
  it.each([...rawColors(css)])("%s cabe no sRGB", (_name, value) => {
    // Fora do gamut o navegador corta o croma e a cor medida aqui não seria a desenhada.
    expect(displayable(value)).toBe(true);
  });

  it("existem as cinco famílias da paleta, com 11 tons cada", () => {
    const families = ["moss", "brass", "stone", "ember", "patina"].map(
      (family) => [...rawColors(css).keys()].filter((name) => name.startsWith(`${family}-`)).length,
    );
    expect(families).toEqual([11, 11, 11, 11, 11]);
  });
});

describe("papéis de cor", () => {
  // Musgo é a cor do que se clica (PUI): sucesso tem matiz próprio, a pátina.
  it.each(THEMES)("sucesso usa pátina no tema $name", ({ selector }) => {
    const block = declarationLines(css, `${selector} {`).join("\n");
    expect(block).toMatch(/--success-bg:\s*var\(--color-patina-\d+\)/);
    expect(block).toMatch(/--success-fg:\s*var\(--color-patina-\d+\)/);
  });

  // Progresso e sucesso são estado, não ação: no musgo pareceriam clicáveis.
  it.each([...THEMES.map(({ selector }) => selector), ":root:not([data-theme])"])(
    "nenhum papel de estado aponta para o musgo em %s",
    (selector) => {
      const stateRoles = declarationLines(css, `${selector} {`).filter((line) =>
        /^--(?:success-[a-z]+|progress-fill):/.test(line),
      );
      expect(stateRoles.length).toBeGreaterThanOrEqual(STATE_ROLE_COUNT);
      expect(stateRoles.filter((line) => line.includes("--color-moss-"))).toEqual([]);
    },
  );

  it.each(THEMES)(
    "o fundo escurecido do diálogo tem papel próprio no tema $name",
    ({ selector }) => {
      expect(semanticColors(css, selector).get("overlay")).toBeDefined();
    },
  );
});

describe("fallback do tema escuro", () => {
  // Sem theme-init.js (bloqueado, falha de rede) não há data-theme, e o sistema escuro precisa
  // do mesmo bloco: as duas cópias não podem divergir.
  it("repete o bloco escuro, declaração por declaração", () => {
    const fallback = declarationLines(css, ":root:not([data-theme]) {");
    expect(fallback.length).toBeGreaterThan(20);
    expect(fallback).toEqual(declarationLines(css, '[data-theme="dark"] {'));
  });
});

function declarationLines(source: string, opening: string): string[] {
  const start = source.indexOf(opening);
  if (start === -1) {
    throw new Error(`tokens.css não tem o bloco ${opening}`);
  }
  const body = source.slice(start + opening.length, source.indexOf("}", start));
  return body
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line.startsWith("--") || line.startsWith("color-scheme"));
}

function colorOf(colors: ReadonlyMap<string, string>, token: string): string {
  const value = colors.get(token);
  if (value === undefined || parse(value) === undefined) {
    throw new Error(`token --${token} ausente ou sem cor resolvível`);
  }
  return value;
}
