// Preview dos resultados na tela da execução (2.2; ADR 037): a imagem reduzida chega pelo <img>
// sob a CSP de produção, e a amostra CSV vira tabela de texto, com fórmula e HTML inertes.
import type { Page } from "@playwright/test";

import { PREVIEW_JPEG } from "../fixtures/files";
import { ExecucaoPage } from "../pages/ExecucaoPage";
import type { SeededOutput } from "../support/fakeApi";
import { expect, test } from "../support/test";

const PHOTO: SeededOutput = {
  name: "foto.png",
  body: Buffer.from("png-de-mentira"),
  preview: { name: "foto.jpg", body: PREVIEW_JPEG, contentType: "image/jpeg" },
};

const FORMULA = "=cmd|' /C calc'!A0";
const HTML = "<img src=x onerror=alert(1)>";
// Como o CSV Janitor escreve a amostra: fórmula com o tab na frente, entre aspas (preview.py).
const SAMPLE = `nome,formula,obs\nAna,"\t${FORMULA}",${HTML}\n`;

const TABLE: SeededOutput = {
  name: "clientes.csv",
  body: Buffer.from(SAMPLE),
  preview: {
    name: "clientes.csv",
    body: Buffer.from(SAMPLE),
    contentType: "application/octet-stream",
  },
};

/** Violações de CSP e diálogos (um `alert` do HTML da amostra) durante a spec. */
function watchUnsafe(page: Page): string[] {
  const problems: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error" && /Content Security Policy/i.test(message.text())) {
      problems.push(message.text());
    }
  });
  page.on("dialog", (dialog) => {
    problems.push(`diálogo: ${dialog.message()}`);
    void dialog.dismiss();
  });
  return problems;
}

test("imagem: o preview aparece junto da saída, sem virar download", async ({ page, fakeApi }) => {
  const problems = watchUnsafe(page);
  const execucao = new ExecucaoPage(page);
  await execucao.open(fakeApi.seedRun({ status: "succeeded", recipe_id: "image-forge" }, [PHOTO]));

  const image = page.getByRole("img", { name: "Pré-visualização de foto.png" });
  await expect(image).toBeVisible();
  // Decodificada de verdade: o JPEG passou pela CSP (img-src 'self') e pelo nosniff da API.
  await expect
    .poll(() => image.evaluate((element: HTMLImageElement) => element.naturalWidth))
    .toBeGreaterThan(0);
  await expect(page.getByText("Carregando a pré-visualização.")).toHaveCount(0);
  await expect(page.getByRole("link", { name: /^Baixar / })).toHaveCount(1);
  await expect(page.getByRole("link", { name: /foto\.jpg/ })).toHaveCount(0);
  expect(problems).toEqual([]);
});

test("CSV: a amostra vira tabela e a fórmula aparece como texto", async ({ page, fakeApi }) => {
  const problems = watchUnsafe(page);
  const execucao = new ExecucaoPage(page);
  await execucao.open(fakeApi.seedRun({ status: "succeeded" }, [TABLE]));

  const table = page.getByRole("table", { name: "Amostra de clientes.csv: 1 registro" });
  await expect(table).toBeVisible();
  await expect(table.getByRole("columnheader")).toHaveText(["nome", "formula", "obs"]);
  const cells = table.getByRole("cell");
  await expect(cells.nth(1)).toHaveText(FORMULA);
  await expect(cells.nth(2)).toHaveText(HTML);
  await expect(table.locator("img")).toHaveCount(0);
  expect(problems).toEqual([]);
});
