// Presets de ponta a ponta: salvar os parâmetros na Bancada, achar o preset na Oficina, abrir a
// Bancada pré-preenchida e excluir com confirmação.
import { BancadaPage } from "../pages/BancadaPage";
import { fulfillProblem } from "../support/fakeAccount";
import { holdRequest } from "../support/held";
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
  const held = holdRequest(page, "**/api/v1/presets", "POST");

  await page.getByRole("button", { name: "Salvar preset" }).click();
  const dialog = page.getByRole("dialog", { name: "Salvar os parâmetros como preset" });
  await dialog.getByLabel("Nome do preset").fill("Clientes do mês");
  await dialog.getByRole("button", { name: "Salvar preset" }).click();
  const save = await held;
  // O Esc só depois de a tela mostrar o pedido em voo.
  await expect(dialog.getByRole("button", { name: "Salvando…" })).toBeVisible();
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

test("Esc com a exclusão em voo não fecha; a falha aparece na Oficina e o diálogo reabre", async ({
  page,
}) => {
  const bancada = new BancadaPage(page);
  await bancada.open("csv-janitor");
  await page.getByRole("button", { name: "Salvar preset" }).click();
  await page.getByLabel("Nome do preset").fill("Clientes do mês");
  await page.getByLabel("Nome do preset").press("Enter");
  await expect(page.getByText("Preset Clientes do mês salvo.")).toBeVisible();
  await page.goto("/");
  const presets = page.getByRole("region", { name: "Presets" });
  const held = holdRequest(page, "**/api/v1/presets/*", "DELETE");

  const remove = presets.getByRole("button", { name: "Excluir o preset Clientes do mês" });
  await remove.click();
  const dialog = page.getByRole("dialog", { name: "Excluir o preset Clientes do mês?" });
  const confirm = dialog.getByRole("button", { name: "Excluir preset" });
  await confirm.click();
  const deletion = await held;
  // O Esc só depois de a tela mostrar o pedido em voo.
  await expect(confirm).toHaveAttribute("aria-disabled", "true");
  await page.keyboard.press("Escape");
  await expect(dialog).toBeVisible();
  await fulfillProblem(deletion, { code: "SERVICE_UNAVAILABLE", status: 503 });

  await expect(presets.getByRole("alert")).toContainText("O serviço está indisponível");
  await expect(dialog).toBeHidden();
  await remove.click();
  await expect(dialog).toBeVisible();
});
