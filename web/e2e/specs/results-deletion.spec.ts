// Excluir os resultados de uma execução concluída: confirmação, anúncio e a tela sem downloads.
import AxeBuilder from "@axe-core/playwright";

import { ExecucaoPage } from "../pages/ExecucaoPage";
import { expect, test } from "../support/test";

test("excluir resultados tira os downloads e diz por quê", async ({ page, fakeApi }) => {
  const execucao = new ExecucaoPage(page);
  await execucao.open(fakeApi.seedRun({ status: "succeeded" }));
  const results = page.getByRole("region", { name: "Resultados" });
  await expect(results.getByRole("link", { name: /^Baixar csv-janitor\.zip/ })).toBeVisible();

  await results.getByRole("button", { name: "Excluir resultados" }).click();
  const dialog = page.getByRole("dialog", { name: "Excluir os resultados desta execução?" });
  await expect(dialog.getByRole("button", { name: "Cancelar" })).toBeFocused();
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await dialog.getByRole("button", { name: "Excluir resultados" }).click();

  await expect(results.getByRole("heading", { name: "Resultados excluídos" })).toBeVisible();
  await expect(results.getByRole("link", { name: /^Baixar/ })).toHaveCount(0);
  await expect(results.getByRole("heading", { name: "Resultados", level: 2 })).toBeFocused();

  // Voltar à tela mais tarde: a disponibilidade vem da API, não da memória da página.
  await page.reload();
  await expect(results.getByRole("heading", { name: "Resultados excluídos" })).toBeVisible();
});
