// A chave de API do E2E só existe no processo de teste (plano da fatia 7, 4.2; decisão U2): o
// navegador nunca a manda por conta própria, e ela não aparece no HTML, nos assets, no storage
// nem nos cookies. No modo local a chave é um canário `CANARY-<uuid>` novo a cada execução.
import { CSV_FILES } from "../fixtures/files";
import { BancadaPage } from "../pages/BancadaPage";
import { E2E } from "../support/env";
import { installFakeApi } from "../support/fakeApi";
import { expect, test } from "../support/test";

/** Maior atraso do primeiro retry em api/queryRetry.ts (1 s mais até 50% de jitter), com folga. */
const CATALOG_QUIET_MS = 2_000;
const CATALOG_QUIET_TIMEOUT_MS = 10_000;

interface ServedBody {
  readonly contentType: string;
  readonly body: string;
}

test("a chave não aparece no HTML, nos assets, no storage nem nos cookies", async ({
  page,
  context,
  apiKey,
}) => {
  // Corpo que não pôde ser lido fica `null` e não conta: a prova precisa ter visto o conteúdo.
  const bodies: Promise<ServedBody | null>[] = [];
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
      const contentType = response.headers()["content-type"] ?? "";
      bodies.push(
        response.text().then(
          (body) => ({ contentType, body }),
          () => null,
        ),
      );
    }
  });

  const bancada = new BancadaPage(page);
  await bancada.open("csv-janitor");
  await bancada.addFiles(CSV_FILES);

  const served = (await Promise.all(bodies)).filter(
    (item): item is ServedBody => item !== null && item.body.length > 0,
  );
  expect(served.length, "HTML, theme-init.js, JS e CSS lidos").toBeGreaterThanOrEqual(4);
  // Quatro fontes ou imagens não provam nada: a chave escaparia pelo HTML ou pelo bundle.
  expect(
    served.some(({ contentType }) => contentType.startsWith("text/html")),
    "o HTML da página foi lido",
  ).toBe(true);
  expect(
    served.some(({ contentType }) => contentType.includes("javascript")),
    "algum JavaScript foi lido",
  ).toBe(true);
  // Booleanos: uma falha imprimiria o valor comparado (corpo, storage, cookies) no relatório HTML,
  // e a chave nunca pode ir parar lá.
  expect(
    served.some(({ body }) => body.includes(apiKey)),
    "[sabotage:api-key-in-bundle]",
  ).toBe(false);
  const storage = await page.evaluate(() =>
    JSON.stringify({
      local: Object.entries(localStorage),
      session: Object.entries(sessionStorage),
    }),
  );
  expect(storage.includes(apiKey), "o storage contém a chave").toBe(false);
  expect(
    JSON.stringify(await context.cookies()).includes(apiKey),
    "algum cookie contém a chave",
  ).toBe(false);
  expect(credentialsFromPage, "pedidos com Authorization montado pelo bundle").toEqual([]);
});

// Monta a API falsa à mão, sem o fixture `fakeApi`; a tag @fake-api o tira do modo remoto.
test("sem a chave do processo de teste, a API recusa e a Bancada convida a entrar @fake-api", async ({
  browser,
  apiKey,
}) => {
  if (E2E.artifactsDir === null) {
    throw new Error("@fake-api coletado sem a API falsa: o modo remoto deveria excluir a tag");
  }
  // Contexto sem o fixture `api`: só a API falsa, que exige a chave, e nenhuma injeção.
  const context = await browser.newContext();
  await installFakeApi(context, apiKey, E2E.artifactsDir);
  const page = await context.newPage();
  const catalogStatuses: number[] = [];
  let catalogRequests = 0;
  let lastCatalogAt: number | null = null;
  // A quietude conta do pedido e da resposta: num CI lento, a resposta de um retry pode chegar
  // depois da janela, mas o pedido não.
  page.on("request", (request) => {
    if (request.url().endsWith("/api/v1/catalog")) {
      catalogRequests += 1;
      lastCatalogAt = Date.now();
    }
  });
  page.on("response", (response) => {
    if (response.url().endsWith("/api/v1/catalog")) {
      catalogStatuses.push(response.status());
      lastCatalogAt = Date.now();
    }
  });
  await page.goto("/bancada/csv-janitor");

  // Sem login, o 401 é o estado normal: convite para entrar, não erro de sessão.
  const title = page.getByRole("heading", { name: "Entre para continuar" });
  await expect(title).toBeVisible();
  const sectionRole = await title.evaluate((heading) =>
    heading.closest("section")?.getAttribute("role"),
  );
  expect(sectionRole, "[sabotage:sign-in-prompt-role]").not.toBe("alert");
  const prompt = page.getByRole("region", { name: "Entre para continuar" });
  await expect(prompt).toBeVisible();
  await expect(prompt.getByRole("link", { name: "Entrar com Google" })).toHaveAttribute(
    "href",
    "/auth/login/google?return_to=%2Fbancada%2Fcsv-janitor",
  );
  // Um retry chegaria até 1,5 s depois do 401 (1 s mais 50% de jitter, api/queryRetry.ts): só
  // depois de a leitura do catálogo ficar quieta por mais que isso o retrato abaixo vale. Um novo
  // pedido reinicia a contagem, então a espera acompanha a rede em vez de um tempo fixo.
  await expect
    .poll(() => (lastCatalogAt === null ? 0 : Date.now() - lastCatalogAt), {
      message: "a leitura do catálogo fica quieta além do backoff de retry",
      timeout: CATALOG_QUIET_TIMEOUT_MS,
    })
    .toBeGreaterThan(CATALOG_QUIET_MS);
  await expect(page.getByRole("alert")).toHaveCount(0);
  await expect(page.getByText("Sua sessão terminou.", { exact: false })).toHaveCount(0);
  // 401 não é falha transitória: a leitura não se repete (api/queryRetry.ts).
  expect(catalogRequests).toBe(1);
  expect(catalogStatuses).toEqual([401]);
  await context.close();
});
