import { describe, expect, it } from "vitest";

import baseCss from "./base.css?raw";

/*
 * A cascata do base.css medida no próprio arquivo: o jsdom não aplica especificidade nem @layer,
 * e o que decide qual borda ou cor o campo mostra é a especificidade dos seletores da mesma camada.
 */

type Specificity = readonly [ids: number, classes: number, types: number];

interface CssRule {
  readonly selectors: readonly string[];
  readonly body: string;
}

const CONTROL_RULE_MARK = "border: 2px solid var(--border-ui)";
const INVALID_SELECTOR = '[aria-invalid="true"]';
const CONTROLS = ["input", "select", "textarea"] as const;
const ZERO: Specificity = [0, 0, 0];

const rules = parseRules(baseCss);

describe("specificityOf, a régua dos testes abaixo", () => {
  it.each<[string, Specificity]>([
    ["input", [0, 0, 1]],
    ['[aria-invalid="true"]', [0, 1, 0]],
    ["input:disabled", [0, 1, 1]],
    ['input:not([type="file"], [type="range"])', [0, 1, 1]],
    ['input:where(:not([type="file"]))', [0, 0, 1]],
    ["summary::-webkit-details-marker", [0, 0, 2]],
    [".card-link:focus-visible::after", [0, 2, 1]],
  ])("%s", (selector, expected) => {
    expect(specificityOf(selector)).toEqual(expected);
  });
});

describe("campo com erro", () => {
  // Com input:not(...) a regra da borda normal pesa (0,1,1) e vence [aria-invalid] (0,1,0).
  it.each(controlSelectors())("a borda de erro vence a borda normal em %s", (selector) => {
    expect(compare(specificityOf(INVALID_SELECTOR), specificityOf(selector))).toBeGreaterThan(0);
  });

  it("pinta a borda na cor de perigo", () => {
    expect(ruleFor(INVALID_SELECTOR).body).toContain("border-color: var(--danger-fg)");
  });
});

describe("campo desabilitado", () => {
  // Sem regra própria o base.css cobre o estilo do navegador: parece editável e não é.
  it.each(CONTROLS)("%s desabilitado muda de cor e de cursor", (control) => {
    const { body } = ruleFor(`${control}:disabled`);

    expect(body).toContain("color: var(--fg-subtle)");
    expect(body).toContain("cursor: not-allowed");
  });

  // O campo vazio não tem texto para mostrar a cor: quem avisa é o fundo, a borda e o relevo.
  it.each(CONTROLS)("%s desabilitado muda fundo, borda e relevo", (control) => {
    const { body } = ruleFor(`${control}:disabled`);

    expect(body).toContain("background-color: var(--surface)");
    expect(body).toContain("border-style: dashed");
    expect(body).toContain("box-shadow: none");
  });

  // :disabled pesa mais que [aria-invalid]: se trocasse a cor da borda, apagaria a borda de erro.
  it("não troca a cor da borda, para a borda de erro continuar valendo", () => {
    expect(ruleFor("input:disabled").body).not.toContain("border-color");
  });

  it.each(controlSelectors())("a cor de desabilitado vence a cor normal em %s", (selector) => {
    expect(compare(specificityOf("input:disabled"), specificityOf(selector))).toBeGreaterThan(0);
  });
});

function controlSelectors(): readonly string[] {
  const rule = rules.find(({ body }) => body.includes(CONTROL_RULE_MARK));
  if (rule === undefined) {
    throw new Error("base.css não tem a regra da borda dos controles");
  }
  return rule.selectors;
}

function ruleFor(selector: string): CssRule {
  const rule = rules.find(({ selectors }) => selectors.includes(selector));
  if (rule === undefined) {
    throw new Error(`base.css não tem regra para ${selector}`);
  }
  return rule;
}

/** Só as regras folha: blocos como @layer e @media entram pelas regras de dentro. */
function parseRules(css: string): CssRule[] {
  const source = css.replace(/\/\*[\s\S]*?\*\//g, "");
  return [...source.matchAll(/([^{}]+)\{([^{}]*)\}/g)].map(([, selectors = "", body = ""]) => ({
    selectors: splitTopLevel(selectors),
    body,
  }));
}

function splitTopLevel(list: string): string[] {
  const parts: string[] = [];
  let depth = 0;
  let current = "";
  for (const char of list) {
    depth += char === "(" ? 1 : char === ")" ? -1 : 0;
    if (char === "," && depth === 0) {
      parts.push(current);
      current = "";
    } else {
      current += char;
    }
  }
  parts.push(current);
  return parts.map((part) => part.trim().replace(/\s+/g, " "));
}

/** Especificidade pelas regras do CSS: `:where` não pesa; `:not`, `:is` e `:has`, o argumento maior. */
function specificityOf(selector: string): Specificity {
  const functional = /:(where|not|is|has)\(/.exec(selector);
  if (functional === null) {
    return countSimple(selector);
  }
  const argumentStart = functional.index + functional[0].length;
  const argumentEnd = closingParen(selector, argumentStart);
  const remainder = `${selector.slice(0, functional.index)}${selector.slice(argumentEnd + 1)}`;
  if (functional[1] === "where") {
    return specificityOf(remainder);
  }
  const heaviest = splitTopLevel(selector.slice(argumentStart, argumentEnd))
    .map(specificityOf)
    .reduce((best, next) => (compare(next, best) > 0 ? next : best), ZERO);
  return add(specificityOf(remainder), heaviest);
}

function closingParen(text: string, start: number): number {
  let depth = 1;
  for (let index = start; index < text.length; index += 1) {
    depth += text[index] === "(" ? 1 : text[index] === ")" ? -1 : 0;
    if (depth === 0) {
      return index;
    }
  }
  throw new Error(`parêntese sem par em ${text}`);
}

function countSimple(selector: string): Specificity {
  const withoutAttributes = selector.replace(/\[[^\]]*\]/g, "");
  const attributes = (selector.match(/\[[^\]]*\]/g) ?? []).length;
  const pseudoElements = (withoutAttributes.match(/::[a-z-]+/g) ?? []).length;
  const withoutPseudoElements = withoutAttributes.replace(/::[a-z-]+/g, "");
  const ids = (withoutPseudoElements.match(/#[\w-]+/g) ?? []).length;
  const classes = (withoutPseudoElements.match(/\.[\w-]+/g) ?? []).length;
  const pseudoClasses = (withoutPseudoElements.match(/:[a-z-]+/g) ?? []).length;
  const types = (withoutPseudoElements.match(/(?:^|[\s>+~])[a-z][\w-]*/g) ?? []).length;
  return [ids, attributes + classes + pseudoClasses, types + pseudoElements];
}

function add(first: Specificity, second: Specificity): Specificity {
  return [first[0] + second[0], first[1] + second[1], first[2] + second[2]];
}

/** Positivo se `first` pesa mais, negativo se pesa menos, zero se empatam. */
function compare(first: Specificity, second: Specificity): number {
  return first[0] - second[0] || first[1] - second[1] || first[2] - second[2];
}
