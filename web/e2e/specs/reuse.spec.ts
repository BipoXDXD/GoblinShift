// Reusar a configuração pelo Histórico: a linha diz se os resultados ainda valem e leva à Bancada
// com os parâmetros daquela execução.
import { HistoricoPage } from "../pages/HistoricoPage";
import { expect, test } from "../support/test";

test("Histórico → Reusar configuração → Bancada com os parâmetros da execução", async ({
  page,
  fakeApi,
}) => {
  fakeApi.seedRun({
    status: "succeeded",
    // O Histórico compara com o relógio real do navegador: um dia à frente ainda vale.
    results_expire_at: new Date(Date.now() + 86_400_000).toISOString(),
    parameters: {
      input_delimiter: ",",
      output_delimiter: ";",
      trim_columns: ["email"],
      case_rules: [],
      filters: [],
      deduplicate_by: [],
      output_columns: null,
      formula_guard: "tab_prefix",
    },
  });
  const historico = new HistoricoPage(page);
  await historico.open();
  const [row] = await historico.rows().all();
  if (row === undefined) throw new Error("sem linha no Histórico");
  await expect(row.getByText(/^Resultados até /)).toBeVisible();

  await row.getByRole("link", { name: /^Reusar configuração/ }).click();

  await expect(page.getByText(/^Parâmetros da execução de /)).toBeVisible();
  await expect(page.getByLabel("Delimitador dos arquivos de saída")).toHaveValue(";");
  await expect(page.getByLabel("Colunas sem espaços nas pontas", { exact: true })).toHaveValue(
    "email",
  );
});
