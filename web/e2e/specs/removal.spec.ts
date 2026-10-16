// Remoção na Bancada (spec 2026-10-15, decisão 5): confirmação, Remover todos e a página atrás do
// diálogo sem rolar.
import { CSV_FILES } from "../fixtures/files";
import { BancadaPage } from "../pages/BancadaPage";
import { expect, test } from "../support/test";

test("remover um, cancelar e remover todos", async ({ page }) => {
  const bancada = new BancadaPage(page);
  await bancada.open("csv-janitor");
  await bancada.addFiles(CSV_FILES);

  await page.getByRole("button", { name: `Remover ${CSV_FILES[0].name}` }).click();
  await expect(page.locator("html")).toHaveCSS("overflow", "hidden");
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toBeHidden();
  await expect(page.locator("html")).not.toHaveCSS("overflow", "hidden");
  expect(await bancada.order()).toEqual(CSV_FILES.map((file) => file.name));

  // O segundo, não o primeiro: remover pela posição errada apagaria o primeiro e passaria.
  await bancada.removeFile(CSV_FILES[1].name);
  expect(await bancada.order()).toEqual([CSV_FILES[0].name]);
  // O botão Remover some com o item; o foco vai para a escolha de arquivos, não para o body.
  await expect(page.getByLabel("Escolher arquivos")).toBeFocused();

  await bancada.addFiles([CSV_FILES[1]]);
  await bancada.removeAll();
  await expect(page.getByRole("list", { name: "Ordem de processamento" })).toBeHidden();
  await expect(page.getByLabel("Escolher arquivos")).toBeFocused();
});

test("remover exclui os uploads no servidor, não só na lista", async ({ page, fakeApi }) => {
  const bancada = new BancadaPage(page);
  await bancada.open("csv-janitor");
  await bancada.addFiles(CSV_FILES);
  await bancada.addFiles([CSV_FILES[0]]);

  await bancada.removeFile(CSV_FILES[1].name);
  await expect.poll(() => fakeApi.deletedUploads).toEqual([CSV_FILES[1].name]);

  await bancada.removeAll();
  await expect
    .poll(() => fakeApi.deletedUploads)
    .toEqual([CSV_FILES[1].name, CSV_FILES[0].name, CSV_FILES[0].name]);
});
