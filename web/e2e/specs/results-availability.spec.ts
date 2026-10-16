// Resultados vencidos: a disponibilidade vem do /results, sem download e com o estado vazio.
import AxeBuilder from "@axe-core/playwright";

import { ExecucaoPage } from "../pages/ExecucaoPage";
import { expect, test } from "../support/test";

test("resultados expirados não oferecem download e dizem quando venceram", async ({
  page,
  fakeApi,
}) => {
  const execucao = new ExecucaoPage(page);
  const runId = fakeApi.seedRun({ status: "succeeded" });
  fakeApi.expireResults(runId);
  await execucao.open(runId);
  const results = page.getByRole("region", { name: "Resultados" });

  await expect(results.getByRole("heading", { name: "Resultados expirados" })).toBeVisible();
  await expect(results).toContainText("Os resultados expiraram em");
  await expect(results.getByRole("link", { name: /^Baixar/ })).toHaveCount(0);
  await expect(results.getByRole("button", { name: "Excluir resultados" })).toHaveCount(0);
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
});
