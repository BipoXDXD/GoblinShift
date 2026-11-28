// Faixa de sem conexão (#442): o navegador avisa que caiu, o TanStack Query pausa as consultas e a
// faixa diz o que acontece; quando a conexão volta, ela diz isso por alguns segundos e some.
import { expectNoWcagViolations } from "../support/axe";
import { expect, test, THEMES, useTheme } from "../support/test";

const OFFLINE = "Sem conexão.";
const BACK = "Conexão de volta.";

for (const theme of THEMES) {
  test(`tema ${theme}: sem conexão aparece a faixa e ela some ao voltar @fake-api`, async ({
    page,
    context,
  }) => {
    await useTheme(page, theme);
    await page.goto("/");
    await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
    const status = page.getByRole("status").filter({ hasText: OFFLINE });
    await expect(page.getByText(OFFLINE)).toBeHidden();

    await context.setOffline(true);
    await expect(status).toBeVisible();
    await expectNoWcagViolations(page, `connection-${theme}`);

    await context.setOffline(false);
    const back = page.getByRole("status").filter({ hasText: BACK });
    await expect(back).toBeVisible();
    await expect(page.getByText(OFFLINE)).toBeHidden();
    await expectNoWcagViolations(page, `connection-back-${theme}`);
    // RECONNECTED_MS (5 s) mais folga: depois disso a faixa some sozinha.
    await expect(page.getByText(BACK)).toBeHidden({ timeout: 10_000 });
  });
}
