// A medição do axe que as telas reais (contrast.spec.ts) e a cena 3D (oficina-3d.spec.ts) dividem:
// as regras WCAG 2.2 A/AA, com o contraste conferido à parte.
import AxeBuilder from "@axe-core/playwright";
import type { Page } from "@playwright/test";

import { expect } from "./test";

const WCAG_22_AA = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"];

function describeViolation(violation: {
  id: string;
  nodes: { target: unknown; failureSummary?: string | undefined }[];
}): string {
  const nodes = violation.nodes.map(
    (node) => `${JSON.stringify(node.target)}: ${node.failureSummary ?? ""}`,
  );
  return `${violation.id}\n${nodes.join("\n")}`;
}

/**
 * Zero violações WCAG 2.2 A/AA na página. A falha de contraste sai com `[sabotage:<marcador>]`,
 * o que o sabotage.ts atribui ao defeito plantado; as outras, sem marcador.
 */
export async function expectNoWcagViolations(page: Page, contrastMarker: string): Promise<void> {
  const results = await new AxeBuilder({ page }).withTags(WCAG_22_AA).analyze();

  // Sem isso, um axe que deixou de avaliar o contraste passaria em silêncio.
  const ranContrast = [...results.passes, ...results.violations].some(
    (rule) => rule.id === "color-contrast",
  );
  expect(ranContrast, "a regra color-contrast avaliou algum elemento").toBe(true);
  expect(
    results.violations
      .filter((violation) => violation.id === "color-contrast")
      .map(describeViolation),
    `[sabotage:${contrastMarker}]`,
  ).toEqual([]);
  expect(results.violations.map(describeViolation)).toEqual([]);
}
