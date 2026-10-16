// Presets de ponta a ponta: salvar os parâmetros na Bancada, achar o preset na Oficina, abrir a
// Bancada pré-preenchida e excluir com confirmação.
import type { Route } from "@playwright/test";

import { BancadaPage } from "../pages/BancadaPage";
import { fulfillProblem } from "../support/fakeAccount";
import { expect, test } from "../support/test";

test("salvar na Bancada, abrir pela Oficina já preenchido e excluir", async ({ page, fakeApi }) => {
  const bancada = new BancadaPage(page);
  await bancada.open("csv-janitor");
  await bancada.choose("Delimitador dos arquivos de saída", "Ponto e vírgula (;)");
  await bancada.fill("Colunas sem espaços nas pontas", "nome");

  await page.getByRole("button", { name: "Salvar preset" }).click();
  const saveDialog = page.getByRole("dialog", { name: "Salvar os parâmetros como preset" });
  await saveDialog.getByLabel("Nome do preset").fill("Clientes do mês");
  await saveDialog.getByLabel("Nome do preset").press("Enter");
  await expect(page.getByText("Preset Clientes do mês salvo.")).toBeVisible();
  expect(fakeApi.createdRuns).toEqual([]);

  await page.goto("/");
  const presets = page.getByRole("region", { name: "Presets" });
  await presets.getByRole("link", { name: "Abrir na Bancada o preset Clientes do mês" }).click();
  await expect(page.getByText("Parâmetros do preset Clientes do mês.")).toBeVisible();
  await expect(page.getByLabel("Delimitador dos arquivos de saída")).toHaveValue(";");
  await expect(page.getByLabel("Colunas sem espaços nas pontas", { exact: true })).toHaveValue(
    "nome",
  );

  await page.goto("/");
  await presets.getByRole("button", { name: "Excluir o preset Clientes do mês" }).click();
  await page
    .getByRole("dialog", { name: "Excluir o preset Clientes do mês?" })
    .getByRole("button", { name: "Excluir preset" })
    .click();
  await expect(page.getByText("Preset Clientes do mês excluído.")).toBeVisible();
  await expect(presets.getByRole("heading", { name: "Nenhum preset salvo" })).toBeVisible();
});

test("Esc com o salvamento em voo não esconde o 409, e o diálogo reabre depois", async ({
  page,
}) => {
  const bancada = new BancadaPage(page);
  await bancada.open("csv-janitor");
  // Segura o POST até o Esc: o 409 chega com o pedido já em voo.
  const held = new Promise<Route>((resolve) => {
    void page.route("**/api/v1/presets", async (route) => {
      if (route.request().method() === "POST") {
        resolve(route);
      } else {
        await route.fallback();
      }
    });
  });

  await page.getByRole("button", { name: "Salvar preset" }).click();
  const dialog = page.getByRole("dialog", { name: "Salvar os parâmetros como preset" });
  await dialog.getByLabel("Nome do preset").fill("Clientes do mês");
  await dialog.getByRole("button", { name: "Salvar preset" }).click();
  const save = await held;
  await page.keyboard.press("Escape");
  await expect(dialog).toBeVisible();
  await fulfillProblem(save, { code: "STATE_CONFLICT", status: 409 });

  await expect(
    dialog.getByText("Já existe um preset com esse nome, ou a conta chegou a 100 presets."),
  ).toBeVisible();
  await dialog.getByRole("button", { name: "Cancelar" }).click();
  await expect(dialog).toBeHidden();
  await page.getByRole("button", { name: "Salvar preset" }).click();
  await expect(dialog).toBeVisible();
});
