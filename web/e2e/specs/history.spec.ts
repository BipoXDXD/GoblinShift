// Histórico e execuções recentes sobre GET /runs: a execução criada pela Bancada aparece nos dois
// lugares, a lista pagina pelo cursor da API e os filtros vivem na URL.
import { CSV_FILES } from "../fixtures/files";
import { BancadaPage } from "../pages/BancadaPage";
import { ExecucaoPage } from "../pages/ExecucaoPage";
import { HistoricoPage } from "../pages/HistoricoPage";
import { seedRunHistory } from "../support/screens";
import { expect, test } from "../support/test";

const PAGE_SIZE = 25;

test("a execução criada na Bancada abre pela Oficina e pelo Histórico", async ({ page }) => {
  const bancada = new BancadaPage(page);
  await bancada.open("csv-janitor");
  await bancada.addFiles(CSV_FILES);
  await bancada.run();
  const runUrl = page.url();
  await new ExecucaoPage(page).waitForStatus("Concluída");

  await page.goto("/");
  const recent = page.getByRole("list", { name: "Execuções" }).getByRole("link").first();
  // O \s? tolera o espaço que o Chromium põe antes do texto sr-only (fora do fluxo); o nome
  // continua dizendo a receita e quando foi criada.
  await expect(recent).toHaveAccessibleName(/^CSV Janitor\s?, criada em /);
  await recent.click();
  await expect(page).toHaveURL(runUrl);

  const historico = new HistoricoPage(page);
  await historico.open();
  await historico.rows().first().getByRole("link").click();
  await expect(page).toHaveURL(runUrl);
});

test("Carregar mais traz a página seguinte e anuncia quantas chegaram", async ({
  page,
  fakeApi,
}) => {
  const total = seedRunHistory(fakeApi, PAGE_SIZE);
  const historico = new HistoricoPage(page);
  await historico.open();
  await expect(historico.rows()).toHaveCount(PAGE_SIZE);

  await historico.loadMore();
  await expect(historico.rows()).toHaveCount(total);
  await expect(
    page.getByText(`Mais ${String(total - PAGE_SIZE)} execuções na lista.`),
  ).toBeAttached();
  await expect(page.getByRole("button", { name: "Carregar mais" })).toHaveCount(0);
});

test("filtrar por estado vai para a URL, sobrevive ao reload e só lista esse estado", async ({
  page,
  fakeApi,
}) => {
  const seeded = seedRunHistory(fakeApi);
  const historico = new HistoricoPage(page);
  await historico.open();
  await expect(historico.rows()).toHaveCount(seeded);

  await historico.filterByStatus("Falhou");
  await expect(page).toHaveURL(/\/historico\?estado=failed$/);
  await expect(historico.rows()).toHaveCount(1);
  await expect(historico.rows().first()).toContainText("Falhou");

  await page.reload();
  await expect(historico.rows()).toHaveCount(1);
  await expect(page.getByLabel("Estado", { exact: true })).toHaveValue("failed");
});
