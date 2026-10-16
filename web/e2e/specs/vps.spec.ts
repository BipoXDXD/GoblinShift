// Jornadas só de leitura contra a VPS (E2E_VPS=1, workflow e2e-vps.yml), no Chromium e no Firefox.
// Também rodam no modo local, contra a API falsa, para não apodrecerem entre um deploy e outro.
//
// Sem conta, o que qualquer visitante vê. Com a chave (secret opcional E2E_VPS_API_KEY), as telas
// que leem dados da conta; sem ela, essas jornadas são puladas com o motivo no relatório. Nenhuma
// jornada muda estado: o fixture `readOnly` reprova qualquer pedido à API que não seja GET.
import AxeBuilder from "@axe-core/playwright";
import type { Page } from "@playwright/test";

import { watchCsp } from "../support/cspWatch";
import { E2E } from "../support/env";
import { expect, test as base } from "../support/test";

const NO_ACCOUNT_REASON =
  "E2E_VPS_API_KEY vazio: as jornadas que exigem conta foram puladas (só as anônimas rodaram).";

const test = base.extend<{ readOnly: undefined }>({
  readOnly: [
    async ({ context }, use) => {
      const writes: string[] = [];
      context.on("request", (request) => {
        const { pathname } = new URL(request.url());
        const isApi = pathname.startsWith("/api/") || pathname.startsWith("/auth/");
        if (isApi && !["GET", "HEAD"].includes(request.method())) {
          writes.push(`${request.method()} ${pathname}`);
        }
      });
      await use(undefined);
      expect(writes, "jornada de leitura mandou pedido que muda estado").toEqual([]);
    },
    { auto: true },
  ],
});

/** Abre a rota e confere: o título, nenhuma violação de CSP e nenhuma do axe. */
async function openClean(page: Page, path: string, heading: string | RegExp): Promise<void> {
  const cspViolations = await watchCsp(page);
  await page.goto(path);
  await expect(page.getByRole("heading", { level: 1, name: heading })).toBeVisible();
  expect(await cspViolations(), "violações de CSP").toEqual([]);
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
}

test.describe("sem conta", () => {
  test.use({ auth: "none" });

  test("Oficina", async ({ page }) => {
    await openClean(page, "/", /.+/);
    await expect(page.getByRole("link", { name: /CSV Janitor/ }).first()).toBeVisible();
  });

  test("Entrar oferece os dois provedores", async ({ page }) => {
    await openClean(page, "/entrar", "Entrar");
    await expect(page.getByRole("link", { name: "Entrar com Google" })).toHaveAttribute(
      "href",
      /^\/auth\/login\/google/,
    );
    await expect(page.getByRole("link", { name: "Entrar com GitHub" })).toHaveAttribute(
      "href",
      /^\/auth\/login\/github/,
    );
  });

  for (const [path, heading] of [
    ["/termos", "Termos de Uso"],
    ["/privacidade", "Aviso de Privacidade"],
    ["/creditos", "Créditos"],
  ] as const) {
    test(`${heading} abre`, async ({ page }) => {
      await openClean(page, path, heading);
    });
  }

  test("Bancada sem sessão convida a entrar, sem formulário", async ({ page }) => {
    await openClean(page, "/bancada/csv-janitor", /CSV Janitor|Bancada/);
    await expect(page.getByRole("region", { name: "Entre para continuar" })).toBeVisible();
    await expect(page.getByLabel("Escolher arquivos")).toHaveCount(0);
  });

  test("rota desconhecida cai na página não encontrada da SPA", async ({ page }) => {
    await openClean(page, "/rota-que-nao-existe-na-vps", "Página não encontrada");
  });

  test("a documentação da API e o contrato respondem", async ({ request }) => {
    test.skip(!E2E.remote, "/docs e /openapi.json vêm da API real; o servidor local só tem a SPA.");
    const docs = await request.get("/docs");
    expect(docs.status()).toBe(200);
    expect(await docs.text()).toContain("swagger-ui");
    const contract = await request.get("/openapi.json");
    expect(contract.status()).toBe(200);
    expect(contract.headers()["content-type"]).toContain("application/json");
  });
});

test.describe("com conta (chave de API)", () => {
  test.skip(!E2E.hasAccount, NO_ACCOUNT_REASON);

  test("Bancada carrega o catálogo e mostra o formulário", async ({ page }) => {
    await openClean(page, "/bancada/csv-janitor", "Bancada: CSV Janitor");
    await expect(page.getByLabel("Escolher arquivos")).toBeVisible();
  });

  test("Histórico lista as execuções ou o vazio", async ({ page }) => {
    await openClean(page, "/historico", "Histórico");
    await expect(
      page
        .getByRole("list", { name: "Execuções" })
        .or(page.getByRole("heading", { name: "Nenhuma execução ainda" })),
    ).toBeVisible();
  });

  test("Acesso pela chave mostra o uso e as cotas", async ({ page }) => {
    await openClean(page, "/acesso", "Acesso");
    await expect(page.getByRole("region", { name: "Uso e cotas" })).toBeVisible();
  });
});
