// Aviso global (ADR 031), contra o build com e2e/fixtures/notice.json (npm run e2e:notice): a faixa
// aparece em todas as telas, com ou sem sessão, entre a barra e o conteúdo, sem tomar o foco; e,
// por ser mudança nos termos, fechada continua fechada depois de recarregar.
import type { Page } from "@playwright/test";

import { SCREENS } from "../support/screens";
import { expect, test } from "../support/test";

const REGION_NAME = "Aviso: mudança nos termos";

function notice(page: Page) {
  return page.getByRole("region", { name: REGION_NAME });
}

for (const screen of SCREENS) {
  test(`${screen.name}: a faixa do aviso fica entre a barra e o conteúdo`, async ({
    page,
    fakeApi,
  }) => {
    await screen.open(page, fakeApi);

    // `includeHidden`: com o diálogo modal aberto, o resto da página sai da árvore acessível.
    const band = page.getByRole("region", { name: REGION_NAME, includeHidden: true });
    await expect(band).toBeVisible();
    await expect(band).toContainText("Os termos de uso vão mudar");
    const header = await page.getByRole("banner", { includeHidden: true }).boundingBox();
    const bandBox = await band.boundingBox();
    const main = await page.getByRole("main", { includeHidden: true }).boundingBox();
    expect(header).not.toBeNull();
    expect(bandBox).not.toBeNull();
    expect(main).not.toBeNull();
    if (header !== null && bandBox !== null && main !== null) {
      expect(bandBox.y).toBeGreaterThanOrEqual(header.y + header.height);
      expect(main.y).toBeGreaterThanOrEqual(bandBox.y + bandBox.height);
    }
  });
}

test("o aviso não toma o foco ao carregar", async ({ page }) => {
  await page.goto("/");
  await expect(notice(page)).toBeVisible();

  // A tela leva o foco ao próprio h1 (FocusHeading); o aviso, com link e botão, não o toma.
  await expect(page.getByRole("heading", { level: 1 })).toBeFocused();
  await expect(notice(page).locator(":focus")).toHaveCount(0);
});

test("Fechar esconde o aviso, e ele continua fechado depois de recarregar", async ({ page }) => {
  await page.goto("/historico");
  await notice(page).getByRole("button", { name: "Fechar" }).click();
  await expect(notice(page)).toHaveCount(0);

  await page.reload();

  await expect(page.getByRole("heading", { level: 1, name: "Histórico" })).toBeVisible();
  await expect(notice(page)).toHaveCount(0);
});
