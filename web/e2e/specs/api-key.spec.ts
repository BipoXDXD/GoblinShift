// A chave de API do E2E só existe no processo de teste (plano da fatia 7, 4.2; decisão U2): o
// navegador nunca a manda por conta própria, e ela não aparece no HTML, nos assets, no storage
// nem nos cookies. No modo local a chave é um canário `CANARY-<uuid>` novo a cada execução.
import { CSV_FILES } from "../fixtures/files";
import { BancadaPage } from "../pages/BancadaPage";
import { E2E } from "../support/env";
import { installFakeApi } from "../support/fakeApi";
import { expect, test } from "../support/test";

test("a chave não aparece no HTML, nos assets, no storage nem nos cookies", async ({
  page,
  context,
}) => {
  const bodies: Promise<string>[] = [];
  const credentialsFromPage: string[] = [];
  page.on("request", (request) => {
    // O evento vem antes das rotas: este é o pedido como o bundle o montou.
    const authorization = request.headers()["authorization"];
    if (authorization !== undefined) {
      credentialsFromPage.push(`${request.method()} ${request.url()}`);
    }
  });
  page.on("response", (response) => {
    if (!new URL(response.url()).pathname.startsWith("/api/")) {
      bodies.push(response.text().catch(() => ""));
    }
  });

  const bancada = new BancadaPage(page);
  await bancada.open("csv-janitor");
  await bancada.addFiles(CSV_FILES);

  const served = await Promise.all(bodies);
  expect(served.length, "HTML, theme-init.js, JS e CSS lidos").toBeGreaterThanOrEqual(4);
  expect(served.filter((body) => body.includes(E2E.apiKey))).toEqual([]);
  const storage = await page.evaluate(() =>
    JSON.stringify({
      local: Object.entries(localStorage),
      session: Object.entries(sessionStorage),
    }),
  );
  expect(storage).not.toContain(E2E.apiKey);
  expect(JSON.stringify(await context.cookies())).not.toContain(E2E.apiKey);
  expect(credentialsFromPage, "pedidos com Authorization montado pelo bundle").toEqual([]);
});

test("sem a chave do processo de teste, a API recusa e a Bancada mostra o erro", async ({
  browser,
}) => {
  test.skip(E2E.artifactsDir === null, "a API falsa só existe no modo local");
  // Contexto sem o fixture `api`: só a API falsa, que exige a chave, e nenhuma injeção.
  const context = await browser.newContext();
  await installFakeApi(context, E2E.apiKey, E2E.artifactsDir ?? "");
  const page = await context.newPage();
  const catalog = page.waitForResponse((response) => response.url().endsWith("/api/v1/catalog"));
  await page.goto("/bancada/csv-janitor");
  expect((await catalog).status()).toBe(401);
  // O TanStack Query repete a consulta 3 vezes (1 + 2 + 4 s) antes de mostrar o erro.
  await expect(page.getByText("Sua sessão terminou. Entre de novo para continuar.")).toBeVisible({
    timeout: 12_000,
  });
  await context.close();
});
