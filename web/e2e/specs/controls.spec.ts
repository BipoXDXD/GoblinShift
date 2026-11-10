// Aparência e tamanho medidos no navegador: o jsdom não aplica a cascata nem faz layout, e uma
// classe presente no elemento não prova que ela teve efeito (min-h em <a> inline, por exemplo).
import type { Locator, Page } from "@playwright/test";

import { BancadaPage } from "../pages/BancadaPage";
import { ExecucaoPage } from "../pages/ExecucaoPage";
import { expect, test, THEMES, useTheme } from "../support/test";

const MIN_TARGET_PX = 44;
const EXTRACT_AUDIO = "Extrair o áudio";
const AUDIO_TRACK = "Faixa de áudio";
const THUMBNAIL_OPTION = "Nenhuma";
const NO_FILES_ERROR = "Arquivos de entrada: escolha pelo menos um arquivo.";
const OUTPUT = { name: "clientes.csv", body: Buffer.from("nome\nAna\n") };

async function lookOf(field: Locator): Promise<Record<"background" | "border" | "shadow", string>> {
  return field.evaluate((element) => {
    const style = getComputedStyle(element);
    return {
      background: style.backgroundColor,
      border: style.borderTopStyle,
      shadow: style.boxShadow,
    };
  });
}

async function heightOf(target: Locator): Promise<number> {
  const box = await target.boundingBox();
  return box?.height ?? 0;
}

async function openMediaMill(page: Page): Promise<BancadaPage> {
  const bancada = new BancadaPage(page);
  await bancada.open("media-mill");
  return bancada;
}

for (const theme of THEMES) {
  test(`Media Mill, tema ${theme}: a faixa de áudio vazia muda de aparência ao ser desabilitada`, async ({
    page,
  }) => {
    await useTheme(page, theme);
    await openMediaMill(page);
    const field = page.getByLabel(AUDIO_TRACK, { exact: true });
    await expect(field).toBeEnabled();
    await expect(field).toHaveValue("");
    const editable = await lookOf(field);

    await page.getByLabel(EXTRACT_AUDIO, { exact: true }).uncheck();
    await expect(field).toBeDisabled();

    const disabled = await lookOf(field);
    expect(disabled.background).not.toBe(editable.background);
    expect(editable.border).toBe("solid");
    expect(disabled.border).toBe("dashed");
    expect(disabled.shadow).toBe("none");
  });
}

test("Media Mill: labels de checkbox e radio têm a altura do alvo de toque", async ({ page }) => {
  await openMediaMill(page);

  expect(await heightOf(page.getByText(EXTRACT_AUDIO, { exact: true }))).toBeGreaterThanOrEqual(
    MIN_TARGET_PX,
  );
  expect(await heightOf(page.getByText(THUMBNAIL_OPTION, { exact: true }))).toBeGreaterThanOrEqual(
    MIN_TARGET_PX,
  );
});

test("Bancada: o link do resumo de erros tem a altura do alvo de toque", async ({ page }) => {
  const bancada = await openMediaMill(page);

  await bancada.runButton().click();

  const link = page.getByRole("link", { name: NO_FILES_ERROR, exact: true });
  await expect(link).toBeVisible();
  expect(await heightOf(link)).toBeGreaterThanOrEqual(MIN_TARGET_PX);
});

test("Moldura: o pular para o conteúdo, com foco, tem a altura do alvo de toque @keyboard", async ({
  page,
}) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();

  // A troca de rota põe o foco no h1 (FocusHeading), então o primeiro Tab não chega ao link;
  // a ordem do Tab já é provada em focus.spec.ts. Aqui basta o estado :focus do link.
  const link = page.getByRole("link", { name: "Pular para o conteúdo", exact: true });
  await link.focus();
  await expect(link).toBeFocused();
  expect(await heightOf(link)).toBeGreaterThanOrEqual(MIN_TARGET_PX);
});

test("Execução: o link de download tem a altura do alvo de toque", async ({ page, fakeApi }) => {
  await new ExecucaoPage(page).open(fakeApi.seedRun({ status: "succeeded" }, [OUTPUT]));

  const link = page.getByRole("link", { name: `Baixar ${OUTPUT.name}` });
  await expect(link).toBeVisible();
  expect(await heightOf(link)).toBeGreaterThanOrEqual(MIN_TARGET_PX);
});
