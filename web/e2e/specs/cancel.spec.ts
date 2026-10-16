// Cancelar na tela da execução: confirmação acessível, o anúncio do pedido e o estado que o
// servidor decide (fila cancela na hora; trabalho em curso passa por "Cancelando").
import AxeBuilder from "@axe-core/playwright";

import { ExecucaoPage } from "../pages/ExecucaoPage";
import { expect, test } from "../support/test";

test("em andamento: confirma, anuncia o pedido e termina cancelada", async ({ page, fakeApi }) => {
  const execucao = new ExecucaoPage(page);
  await execucao.open(
    fakeApi.seedRun({
      status: "running",
      progress: { phase: "processing", processed: 1, total: 3 },
    }),
  );

  await page.getByRole("button", { name: "Cancelar execução" }).click();
  const dialog = page.getByRole("dialog", { name: "Cancelar esta execução?" });
  await expect(dialog.getByRole("button", { name: "Continuar a execução" })).toBeFocused();
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await dialog.getByRole("button", { name: "Cancelar execução" }).click();

  await expect(page.getByRole("status").filter({ hasText: "Cancelamento pedido." })).toBeVisible();
  await execucao.waitForStatus("Cancelada");
  await expect(page.getByRole("button", { name: "Cancelar execução" })).toBeHidden();
});

test("na fila: cancela na hora", async ({ page, fakeApi }) => {
  const execucao = new ExecucaoPage(page);
  await execucao.open(fakeApi.seedRun({ status: "queued" }));

  await page.getByRole("button", { name: "Cancelar execução" }).click();
  await page
    .getByRole("dialog", { name: "Cancelar esta execução?" })
    .getByRole("button", { name: "Cancelar execução" })
    .click();

  await expect(page.getByRole("status").filter({ hasText: "Execução cancelada." })).toBeVisible();
  await execucao.waitForStatus("Cancelada");
});

test("concluída: sem botão de cancelar", async ({ page, fakeApi }) => {
  const execucao = new ExecucaoPage(page);
  await execucao.open(fakeApi.seedRun({ status: "succeeded" }));

  await execucao.waitForStatus("Concluída");
  await expect(page.getByRole("button", { name: "Cancelar execução" })).toHaveCount(0);
});
