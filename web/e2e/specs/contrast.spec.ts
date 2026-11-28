// Contraste e WCAG 2.2 A/AA nas telas reais, nos dois temas (P9). Complementa o contrast.test.ts,
// que mede os pares de tokens: aqui entram a composição, os estados e o texto sobre cada fundo.
// No build com o incidente (fase notice-incident), só as telas em que a faixa vermelha cobre os
// casos distintos: tela comum, lista, formulário com erro e tabela.
import AxeBuilder from "@axe-core/playwright";

import { describeViolation, WCAG_22_AA } from "../support/axe";
import { NOTICE_KIND } from "../support/env";
import { NOTICE_INCIDENT_SCREENS, SCREENS } from "../support/screens";
import { expect, test, THEMES, useTheme } from "../support/test";

const MEASURED_SCREENS = NOTICE_KIND === "incident" ? NOTICE_INCIDENT_SCREENS : SCREENS;
const INCIDENT_BAND = "Aviso: incidente de segurança";

for (const theme of THEMES) {
  for (const screen of MEASURED_SCREENS) {
    test(`${screen.name}, tema ${theme}: zero violações do axe, contraste incluído @fake-api`, async ({
      page,
      fakeApi,
    }) => {
      await useTheme(page, theme);
      await screen.open(page, fakeApi);
      await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
      // Sem a faixa na tela, o axe mediria só a tela e a fase passaria sem medir o incidente.
      if (NOTICE_KIND === "incident") {
        await expect(page.getByRole("region", { name: INCIDENT_BAND })).toBeVisible();
      }

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
        `[sabotage:contrast-${theme}]`,
      ).toEqual([]);
      expect(results.violations.map(describeViolation)).toEqual([]);
    });
  }
}

// O painel do seletor de tema abre por cima do conteúdo: as telas acima o medem fechado.
for (const theme of THEMES) {
  test(`Menu de tema aberto, tema ${theme}: zero violações do axe, contraste incluído`, async ({
    page,
  }) => {
    await useTheme(page, theme);
    await page.goto("/");
    await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
    await page.getByText("Tema", { exact: true }).click();
    await expect(page.getByRole("radio", { name: "Sistema", exact: true })).toBeVisible();

    const results = await new AxeBuilder({ page }).withTags(WCAG_22_AA).analyze();

    const ranContrast = [...results.passes, ...results.violations].some(
      (rule) => rule.id === "color-contrast",
    );
    expect(ranContrast, "a regra color-contrast avaliou algum elemento").toBe(true);
    expect(
      results.violations
        .filter((violation) => violation.id === "color-contrast")
        .map(describeViolation),
      `[sabotage:contrast-${theme}]`,
    ).toEqual([]);
    expect(results.violations.map(describeViolation)).toEqual([]);
  });
}
