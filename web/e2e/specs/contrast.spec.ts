// Contraste e WCAG 2.2 A/AA nas telas reais, nos dois temas (P9). Complementa o contrast.test.ts,
// que mede os pares de tokens: aqui entram a composição, os estados e o texto sobre cada fundo.
// No build com o incidente (fase notice-incident), só as telas em que a faixa vermelha cobre os
// casos distintos: tela comum, lista, formulário com erro e tabela.
import { expectNoWcagViolations } from "../support/axe";
import { INCIDENT_BUILD } from "../support/env";
import { NOTICE_INCIDENT_SCREENS, SCREENS } from "../support/screens";
import { expect, test, THEMES, useTheme } from "../support/test";

const MEASURED_SCREENS = INCIDENT_BUILD ? NOTICE_INCIDENT_SCREENS : SCREENS;
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
      if (INCIDENT_BUILD) {
        await expect(page.getByRole("region", { name: INCIDENT_BAND })).toBeVisible();
      }

      await expectNoWcagViolations(page, `contrast-${theme}`);
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

    await expectNoWcagViolations(page, `contrast-${theme}`);
  });
}
