// CSRF pela interface (T19; cookies.py e dependencies.py): toda mutação da sessão leva o header
// X-CSRF-Token com o valor do cookie __Host-gs_csrf, e sem ele a API recusa e a tela pede para
// recarregar, sem perder a sessão.
import { AcessoPage } from "../pages/AcessoPage";
import { expect, test } from "../support/test";

test.use({ auth: "none" });

const CSRF_COOKIE = "__Host-gs_csrf";

test("toda mutação pela sessão leva o X-CSRF-Token igual ao cookie @fake-api", async ({
  page,
  context,
  fakeApi,
}) => {
  const mutations: { route: string; token: string | undefined }[] = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (request.method() !== "GET" && /^\/(api|auth)\//.test(url.pathname)) {
      mutations.push({
        route: `${request.method()} ${url.pathname}`,
        token: request.headers()["x-csrf-token"],
      });
    }
  });
  await fakeApi.signIn();
  const csrf = (await context.cookies()).find((cookie) => cookie.name === CSRF_COOKIE)?.value;
  const acesso = new AcessoPage(page);
  await acesso.open();

  await acesso.createKey(["Ler arquivos (files:read)"]);
  expect(mutations[0]?.route).toBe("POST /api/v1/api-keys");
  expect(csrf).toBeDefined();
  expect(mutations[0]?.token === csrf, "[sabotage:csrf-mutation-header]").toBe(true);
  await page.getByRole("button", { name: "Já copiei" }).click();
  const [stored] = [...fakeApi.account.keys.values()];
  const dialog = await acesso.askRevoke(stored?.key.public_id ?? "");
  await dialog.getByRole("button", { name: "Revogar chave" }).click();
  await expect(acesso.keyItems().first()).toContainText("Revogada");
  await acesso.signOut();
  await expect(page).toHaveURL(/\/$/);

  expect(mutations.map((mutation) => mutation.route)).toEqual([
    "POST /api/v1/api-keys",
    `DELETE /api/v1/api-keys/${stored?.key.id ?? ""}`,
    "POST /auth/logout",
  ]);
  expect(csrf).toBeDefined();
  expect(mutations.map((mutation) => mutation.token)).toEqual([csrf, csrf, csrf]);
  expect(fakeApi.account.csrfFailures).toEqual([]);
});

test("sem o cookie de CSRF, a API recusa, a tela pede para recarregar e a sessão continua @fake-api", async ({
  page,
  context,
  fakeApi,
}) => {
  await fakeApi.signIn();
  const acesso = new AcessoPage(page);
  await acesso.open();
  await context.clearCookies({ name: CSRF_COOKIE });

  await acesso.signOut();

  // A seção também tem a região assertiva (vazia) da falha ao copiar o identificador: o filtro
  // pelo texto mira o ProblemAlert sem violar o strict mode.
  await expect(
    acesso
      .section("Sessão")
      .getByRole("alert")
      .filter({ hasText: "A verificação de segurança da sessão falhou." }),
  ).toHaveText(/A verificação de segurança da sessão falhou\. Recarregue a página/);
  expect(fakeApi.account.csrfFailures).toEqual(["POST /auth/logout"]);
  await expect(page).toHaveURL(/\/acesso$/);
  expect((await context.cookies()).map((cookie) => cookie.name)).toEqual(["__Host-gs_session"]);
});
