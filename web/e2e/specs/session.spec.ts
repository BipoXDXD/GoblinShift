// Login, aceite dos termos e saída pela interface (T15, T16, T19), com a sessão por cookie e o CSRF
// da API falsa (support/fakeAccount.ts). O login no Google e no GitHub não roda no CI (detecção de
// robô): a API falsa responde o /auth/login como o callback, e o resto é o fluxo real da SPA.
import { AcessoPage } from "../pages/AcessoPage";
import { BancadaPage } from "../pages/BancadaPage";
import { EntrarPage } from "../pages/EntrarPage";
import { expect, test } from "../support/test";

test.use({ auth: "none" });

test("sem sessão, entra pelo cabeçalho, aceita os termos e volta à Bancada", async ({
  page,
  fakeApi,
}) => {
  // A conta ainda não aceitou os termos: o login volta e o Layout leva ao aceite.
  fakeApi.account.termsAccepted = false;
  await page.goto("/bancada/csv-janitor");
  await expect(page.getByRole("region", { name: "Entre para continuar" })).toBeVisible();

  const entrar = new EntrarPage(page);
  await entrar.openFromHeader();
  await expect(page).toHaveURL(/\/entrar\?volta=%2Fbancada%2Fcsv-janitor$/);
  await entrar.signInWith("google");

  await expect(page).toHaveURL(/\/termos\/aceitar\?volta=%2Fbancada%2Fcsv-janitor$/);
  await expect(page.getByRole("heading", { level: 1, name: "Termos de uso" })).toBeFocused();
  await page.getByRole("button", { name: "Aceitar e continuar" }).click();

  await expect(page).toHaveURL(/\/bancada\/csv-janitor$/);
  await expect(new BancadaPage(page).runButton()).toBeVisible();
  await expect(
    page.getByRole("navigation", { name: "Principal" }).getByRole("link", { name: "Acesso" }),
  ).toBeVisible();
});

test("login recusado volta à Oficina com o erro literal", async ({ page, fakeApi }) => {
  fakeApi.account.refuseNextLogin = true;
  await page.goto("/entrar");

  await new EntrarPage(page).signInWith("github");

  await expect(page).toHaveURL(/\/\?login_error=failed$/);
  await expect(page.getByRole("alert")).toHaveText(/^Não foi possível entrar\./);
});

test("Sair encerra a sessão: os cookies somem e a próxima tela pede para entrar", async ({
  page,
  context,
  fakeApi,
}) => {
  await fakeApi.signIn();
  const acesso = new AcessoPage(page);
  await acesso.open();

  await acesso.signOut();

  await expect(page).toHaveURL(/\/$/);
  await expect(
    page.getByRole("navigation", { name: "Principal" }).getByRole("link", { name: "Entrar" }),
  ).toBeVisible();
  expect(await context.cookies()).toEqual([]);
  await page.goto("/historico");
  await expect(page.getByRole("region", { name: "Entre para continuar" })).toBeVisible();
});

test("Sair de todos: Esc cancela e devolve o foco; confirmar revoga as chaves @keyboard", async ({
  page,
  context,
  fakeApi,
}) => {
  await fakeApi.signIn();
  const acesso = new AcessoPage(page);
  await acesso.open();
  await acesso.createKey(["Ver execuções (runs:read)"]);
  await page.getByRole("button", { name: "Já copiei" }).click();

  const opener = acesso
    .section("Sessão")
    .getByRole("button", { name: "Sair de todos os dispositivos" });
  await opener.focus();
  await page.keyboard.press("Enter");
  const dialog = page.getByRole("dialog", { name: "Sair de todos os dispositivos?" });
  await expect(dialog.getByRole("button", { name: "Cancelar" })).toBeFocused();
  // O modal deixa a página inerte: o Tab passa pelos botões do diálogo e nunca cai num elemento
  // da página por trás (o Chromium pode levar o foco à interface do navegador, fora do documento).
  for (let stop = 0; stop < 4; stop += 1) {
    await page.keyboard.press("Tab");
    const focusBehind = await page.evaluate(() => {
      const active = document.activeElement;
      const open = document.querySelector("dialog[open]");
      return active !== null && active !== document.body && open?.contains(active) !== true;
    });
    expect(focusBehind, "foco atrás do diálogo modal").toBe(false);
  }
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  await expect(opener).toBeFocused();

  const confirm = await acesso.askSignOutAll();
  await confirm.getByRole("button", { name: "Sair de todos e revogar as chaves" }).click();

  await expect(page).toHaveURL(/\/$/);
  expect(await context.cookies()).toEqual([]);
  expect([...fakeApi.account.keys.values()].map((stored) => stored.key.revoked_at)).toEqual([
    expect.any(String),
  ]);
});
