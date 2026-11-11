// Reflow a 320 px (WCAG 1.4.10) com nome de arquivo longo e sem espaço: o nome quebra linha e fica
// inteiro à vista, sem rolagem lateral, na Bancada e na Execução. Medido no navegador: o jsdom não
// faz layout, e uma classe no elemento não prova que o texto quebrou.
import type { Locator } from "@playwright/test";

import { BancadaPage } from "../pages/BancadaPage";
import { ExecucaoPage } from "../pages/ExecucaoPage";
import { expectFitsDeviceWidth } from "../support/reflow";
import { expect, test } from "../support/test";

const NARROW = { width: 320, height: 640 } as const;
// 85 caracteres sem espaço: não cabe em 320 px em nenhuma fonte, então só passa se quebrar.
const LONG_NAME =
  "relatorio_clientes_consolidado_2026_novembro_final_revisado_pela_contabilidade_v3.csv";

/** O texto cabe na própria caixa: nada cortado por `truncate` nem vazando para o lado. */
async function isClipped(element: Locator): Promise<boolean> {
  return element.evaluate((node) => node.scrollWidth > node.clientWidth);
}

test("Bancada a 320 px: o nome longo aparece inteiro e a página não rola para o lado", async ({
  page,
}) => {
  await page.setViewportSize(NARROW);
  const bancada = new BancadaPage(page);
  await bancada.open("csv-janitor");

  await bancada.addFiles([
    { name: LONG_NAME, mimeType: "text/csv", buffer: Buffer.from("nome\nAna\n", "utf8") },
  ]);

  const name = bancada.entry(LONG_NAME).getByText(LONG_NAME, { exact: true });
  await expect(name).toBeVisible();
  expect(await isClipped(name), "nome cortado na Bancada").toBe(false);
  await expectFitsDeviceWidth(page, "rolagem lateral na Bancada");
});

test("Execução a 320 px: o link Baixar com nome longo quebra linha, sem rolagem lateral", async ({
  page,
  fakeApi,
}) => {
  await page.setViewportSize(NARROW);
  const runId = fakeApi.seedRun({ status: "succeeded" }, [
    { name: LONG_NAME, body: Buffer.from("nome\nAna\n") },
  ]);

  await new ExecucaoPage(page).open(runId);

  const link = page.getByRole("link", { name: `Baixar ${LONG_NAME}` });
  await expect(link).toBeVisible();
  await expect(link).toHaveText(`Baixar ${LONG_NAME}`);
  expect(await isClipped(link), "link cortado na Execução").toBe(false);
  await expectFitsDeviceWidth(page, "rolagem lateral na Execução");
});
