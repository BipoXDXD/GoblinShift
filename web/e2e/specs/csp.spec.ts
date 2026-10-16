// CSP de produção sem violação (P9; GoblinShiftPlan 3.6). A política vem do snippet do Nginx, não
// de uma cópia: o servidor local a lê de lá, e esta spec confere que é ela que chega ao navegador.
import { CSV_FILES } from "../fixtures/files";
import { BancadaPage } from "../pages/BancadaPage";
import { ExecucaoPage } from "../pages/ExecucaoPage";
import { watchCsp } from "../support/cspWatch";
import { productionCsp } from "../support/nginxHeaders";
import { SCREENS } from "../support/screens";
import { expect, test, THEMES, useTheme } from "../support/test";

test("o documento e os assets chegam com a CSP do snippet de produção", async ({ page }) => {
  const csp = productionCsp();
  // A política proíbe o que o build não precisa; se o snippet afrouxar, esta spec vira inútil.
  // 'wasm-unsafe-eval' é permitido (decoder Meshopt do GLB, ADR 030): só compila WASM, não JS.
  expect(csp).not.toMatch(/unsafe-inline|(?<!wasm-)unsafe-eval|\*/);

  const assetCsps = new Map<string, string | undefined>();
  page.on("response", (response) => {
    if (/\/assets\/.+\.(js|css)$/.test(response.url())) {
      assetCsps.set(response.url(), response.headers()["content-security-policy"]);
    }
  });
  const documentResponse = await page.goto("/");
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();

  expect(documentResponse?.headers()["content-security-policy"]).toBe(csp);
  expect(assetCsps.size).toBeGreaterThan(0);
  for (const [url, assetCsp] of assetCsps) {
    expect(assetCsp, url).toBe(csp);
  }
});

for (const theme of THEMES) {
  for (const screen of SCREENS) {
    test(`${screen.name}, tema ${theme}: nenhuma violação de CSP`, async ({ page, fakeApi }) => {
      const violations = await watchCsp(page);
      await useTheme(page, theme);
      await screen.open(page, fakeApi);
      expect(await violations()).toEqual([]);
    });
  }
}

test("jornada inteira, do envio ao download, sem violação de CSP", async ({ page }) => {
  // Sem estado semeado: roda também contra a API real (E2E_BASE_URL).
  const violations = await watchCsp(page);
  const bancada = new BancadaPage(page);
  await bancada.open("csv-janitor");
  await bancada.addFiles(CSV_FILES);
  await page.getByText("Tema", { exact: true }).click();
  await page.getByRole("radio", { name: "Escuro", exact: true }).check();
  await bancada.run();
  const execucao = new ExecucaoPage(page);
  await execucao.waitForStatus("Concluída");
  await execucao.download("csv-janitor.zip");
  expect(await violations()).toEqual([]);
});
