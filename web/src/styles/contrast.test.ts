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
  ...pairs("success-fg", ["success-bg"], TEXT),
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

  it("existem as quatro famílias da paleta, com 11 tons cada", () => {
    const families = ["moss", "brass", "stone", "ember"].map(
      (family) => [...rawColors(css).keys()].filter((name) => name.startsWith(`${family}-`)).length,
    );
    expect(families).toEqual([11, 11, 11, 11]);
  });
});

function colorOf(colors: ReadonlyMap<string, string>, token: string): string {
  const value = colors.get(token);
  if (value === undefined || parse(value) === undefined) {
    throw new Error(`token --${token} ausente ou sem cor resolvível`);
  }
  return value;
}
