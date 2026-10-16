// Jornadas principais das receitas, de ponta a ponta pela interface: escolher os arquivos,
// reordenar só por botões (alternativa ao arrasto, WCAG 2.5.7), preencher parâmetros, executar,
// acompanhar até concluir e baixar o pacote e um arquivo avulso.
import { statSync } from "node:fs";

import {
  CSV_FILES,
  DOWNLOADS_FILES,
  PDF_FILES,
  PNG_FILES,
  type InputFile,
} from "../fixtures/files";
import { BancadaPage, type RecipeId } from "../pages/BancadaPage";
import { ExecucaoPage } from "../pages/ExecucaoPage";
import { expect, test } from "../support/test";
import type { FakeApi } from "../support/fakeApi";
import type { Page } from "@playwright/test";

interface Journey {
  readonly recipeId: RecipeId;
  readonly files: readonly [InputFile, InputFile];
  readonly fillParameters: (bancada: BancadaPage) => Promise<void>;
  /** Parâmetros que o POST /runs tem que levar depois do preenchimento. */
  readonly expectedParameters: Record<string, unknown>;
  /** Saída avulsa a baixar; `null` quando o nome não vem da entrada (PDF Press: só o pacote). */
  readonly singleDownload?: null;
}

const JOURNEYS: readonly Journey[] = [
  {
    recipeId: "csv-janitor",
    files: CSV_FILES,
    fillParameters: async (bancada) => {
      await bancada.choose("Delimitador dos arquivos de saída", "Ponto e vírgula (;)");
      await bancada.fill("Colunas sem espaços nas pontas", "nome");
    },
    expectedParameters: { output_delimiter: ";", trim_columns: ["nome"] },
  },
  {
    recipeId: "image-forge",
    files: PNG_FILES,
    fillParameters: async (bancada) => {
      await bancada.choose("Formato de saída", "JPEG");
      await bancada.fill("Largura máxima, em pixels", "800");
    },
    expectedParameters: { output_format: "jpeg", max_width: 800 },
  },
  {
    recipeId: "downloads-detox",
    files: DOWNLOADS_FILES,
    fillParameters: async (bancada) => {
      await bancada.choose("Nomes dos arquivos", "Numerar na ordem de entrada");
      await bancada.fill("Prefixo da numeração", "lote");
    },
    expectedParameters: { name_mode: "indexed", name_prefix: "lote" },
  },
  {
    recipeId: "pdf-press",
    files: PDF_FILES,
    fillParameters: async (bancada) => {
      await bancada.choose("O que fazer com os PDFs", "Montar um PDF com as páginas escolhidas");
      await bancada.press("Adicionar página");
      await bancada.choose("PDF da página 1", "contrato.pdf");
    },
    expectedParameters: { operation: "select", select: [{ page_number: 1 }] },
    singleDownload: null,
  },
];

for (const journey of JOURNEYS) {
  test(`${journey.recipeId}: arquivos → reordenar por botões → parâmetros → executar → baixar`, async ({
    page,
    api,
  }) => {
    const [first, second] = journey.files;
    const bancada = new BancadaPage(page);
    await bancada.open(journey.recipeId);

    await bancada.addFiles(journey.files);
    expect(await bancada.order()).toEqual([first.name, second.name]);

    await bancada.moveUp(second.name);
    expect(await bancada.order()).toEqual([second.name, first.name]);
    await expect(page.getByText(`${second.name} movido para a posição 1 de 2.`)).toBeAttached();

    await journey.fillParameters(bancada);
    await bancada.run();

    const execucao = new ExecucaoPage(page);
    await execucao.waitForStatus("Concluída");
    if (api !== null) {
      expectCommand(api, journey, [second.name, first.name]);
    }

    await expectDownload(page, execucao, `${journey.recipeId}.zip`);
    if (journey.singleDownload !== null) {
      await expectDownload(page, execucao, second.name);
    }
  });
}

function expectCommand(api: FakeApi, journey: Journey, order: readonly string[]): void {
  expect(api.createdRuns).toHaveLength(1);
  const [command] = api.createdRuns;
  expect(command?.recipe_id).toBe(journey.recipeId);
  expect((command?.input_upload_ids ?? []).map((id) => api.uploadName(id))).toEqual(order);
  expect(command?.parameters).toMatchObject(journey.expectedParameters);
}

async function expectDownload(page: Page, execucao: ExecucaoPage, fileName: string): Promise<void> {
  const download = await execucao.download(fileName);
  expect(download.suggestedFilename()).toBe(fileName);
  expect(statSync(await download.path()).size).toBeGreaterThan(0);
  // Download por navegação não tira a pessoa da tela da execução.
  await expect(page).toHaveURL(/\/execucoes\//);
}
