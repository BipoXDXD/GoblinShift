// Presets de ponta a ponta: salvar os parâmetros na Bancada, achar o preset na Oficina, abrir a
// Bancada pré-preenchida e excluir com confirmação.
import { BancadaPage } from "../pages/BancadaPage";
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
