// Contraste e WCAG 2.2 A/AA nas telas reais, nos dois temas (P9). Complementa o contrast.test.ts,
// que mede os pares de tokens: aqui entram a composição, os estados e o texto sobre cada fundo.
import AxeBuilder from "@axe-core/playwright";

import { SCREENS } from "../support/screens";
import { expect, test, THEMES, useTheme } from "../support/test";

const WCAG_22_AA = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"];

for (const theme of THEMES) {
  for (const screen of SCREENS) {
    test(`${screen.name}, tema ${theme}: zero violações do axe, contraste incluído`, async ({
      page,
      fakeApi,
    }) => {
      await useTheme(page, theme);
      await screen.open(page, fakeApi);
      await expect(page.locator("html")).toHaveAttribute("data-theme", theme);

      const results = await new AxeBuilder({ page }).withTags(WCAG_22_AA).analyze();

      // Sem isso, um axe que deixou de avaliar o contraste passaria em silêncio.
      const ranContrast = [...results.passes, ...results.violations].some(
        (rule) => rule.id === "color-contrast",
      );
      expect(ranContrast, "a regra color-contrast avaliou algum elemento").toBe(true);
      expect(results.violations.map(describeViolation)).toEqual([]);
    });
  }
}

function describeViolation(violation: {
  id: string;
  nodes: { target: unknown; failureSummary?: string | undefined }[];
}): string {
  const nodes = violation.nodes.map(
    (node) => `${JSON.stringify(node.target)}: ${node.failureSummary ?? ""}`,
  );
  return `${violation.id}\n${nodes.join("\n")}`;
}
