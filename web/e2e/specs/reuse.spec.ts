// Reusar a configuração pelo Histórico: a linha diz se os resultados ainda valem e leva à Bancada
// com os parâmetros daquela execução.
import { HistoricoPage } from "../pages/HistoricoPage";
import { holdRequest } from "../support/held";
import { expect, test } from "../support/test";

const PARAMETERS = {
  input_delimiter: ",",
  output_delimiter: ";",
  trim_columns: ["email"],
  case_rules: [],
  filters: [],
  deduplicate_by: [],
  output_columns: null,
  formula_guard: "tab_prefix",
};

test("Histórico → Reusar configuração → Bancada com os parâmetros da execução", async ({
  page,
  fakeApi,
}) => {
  fakeApi.seedRun({
    status: "succeeded",
    // O Histórico compara com o relógio real do navegador: um dia à frente ainda vale.
    results_expire_at: new Date(Date.now() + 86_400_000).toISOString(),
    parameters: PARAMETERS,
  });
  const historico = new HistoricoPage(page);
  await historico.open();
  // all() não espera a lista chegar; o expect espera a primeira linha.
  const row = historico.rows().first();
  await expect(row.getByText(/^Resultados até /)).toBeVisible();

  await row.getByRole("link", { name: /^Reusar configuração/ }).click();

  await expect(page.getByText(/^Parâmetros da execução de /)).toBeVisible();
  await expect(page.getByLabel("Delimitador dos arquivos de saída")).toHaveValue(";");
  await expect(page.getByLabel("Colunas sem espaços nas pontas", { exact: true })).toHaveValue(
    "email",
  );
});

test("o aviso de origem entra numa região viva que já existia antes de a execução chegar", async ({
  page,
  fakeApi,
}) => {
  const runId = fakeApi.seedRun({ status: "succeeded", parameters: PARAMETERS });
  const held = holdRequest(page, `**/api/v1/runs/${runId}`, "GET");
  await page.goto(`/bancada/csv-janitor?execucao=${runId}`);
  const run = await held;
  await expect(page.getByText("Carregando a execução.")).toBeVisible();
  // Marca as regiões vivas que o leitor de tela já conhece antes de o aviso existir.
  await page.locator('[role="status"]').evaluateAll((regions) => {
    for (const region of regions) {
      region.setAttribute("data-e2e-antes", "");
    }
  });

  await run.fallback();

  await expect(
    page
      .locator('[role="status"][data-e2e-antes]')
      .filter({ hasText: /^Parâmetros da execução de / }),
  ).toHaveCount(1);
  // O parágrafo visível não é região viva: nasceria já com o texto e não seria lido.
  await expect(
    page.locator("p").filter({ hasText: /^Parâmetros da execução de / }),
  ).not.toHaveAttribute("role", "status");
});
