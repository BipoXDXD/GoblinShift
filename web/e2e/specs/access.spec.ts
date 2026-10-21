// Acesso com sessão (T16–T18): chave de API mostrada uma vez e fora do storage, revogação com
// confirmação, reautenticação pelo provedor e exclusão da conta com a frase digitada.
import { AcessoPage } from "../pages/AcessoPage";
import { fulfillProblem } from "../support/fakeAccount";
import { holdRequest } from "../support/held";
import { expect, test } from "../support/test";

test.use({ auth: "none" });

test("a chave nova aparece uma vez, copia, e o segredo não fica em storage, cookie nem console", async ({
  page,
  context,
  fakeApi,
}) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  const consoleTexts: string[] = [];
  page.on("console", (message) => consoleTexts.push(message.text()));
  await fakeApi.signIn();
  const acesso = new AcessoPage(page);
  await acesso.open();

  const reveal = await acesso.createKey([
    "Ver execuções (runs:read)",
    "Criar e cancelar execuções (runs:write)",
  ]);
  const [token = ""] = fakeApi.account.issuedTokens;
  await expect(reveal.getByLabel("Chave de API")).toHaveValue(token);
  await reveal.getByRole("button", { name: "Copiar chave" }).click();
  await expect(reveal.getByRole("status")).toHaveText("Chave copiada.");
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(token);

  const canary = token.slice(token.indexOf("CANARY-"));
  const stored = await page.evaluate(() =>
    JSON.stringify([Object.entries(localStorage), Object.entries(sessionStorage), location.href]),
  );
  expect(stored).not.toContain(canary);
  expect(JSON.stringify(await context.cookies())).not.toContain(canary);
  expect(consoleTexts.join("\n")).not.toContain(canary);

  await reveal.getByRole("button", { name: "Já copiei" }).click();
  await expect(page.getByLabel("Chave de API")).toHaveCount(0);
  await page.reload();
  await expect(acesso.keyItems()).toHaveCount(1);
  expect(await page.content()).not.toContain(canary);
});

test("revogar pede confirmação: Cancelar mantém, confirmar revoga e a lista mostra", async ({
  page,
  fakeApi,
}) => {
  await fakeApi.signIn();
  const acesso = new AcessoPage(page);
  await acesso.open();
  await acesso.createKey(["Ler arquivos (files:read)"]);
  await page.getByRole("button", { name: "Já copiei" }).click();
  const [stored] = [...fakeApi.account.keys.values()];
  const publicId = stored?.key.public_id ?? "";

  const cancelled = await acesso.askRevoke(publicId);
  await expect(cancelled.getByRole("button", { name: "Cancelar" })).toBeFocused();
  await cancelled.getByRole("button", { name: "Cancelar" }).click();
  expect(stored?.key.revoked_at).toBeNull();

  const confirm = await acesso.askRevoke(publicId);
  await confirm.getByRole("button", { name: "Revogar chave" }).click();

  await expect(page.getByText(`Chave ${publicId} revogada.`)).toBeAttached();
  await expect(acesso.keyItems().first()).toContainText("Revogada");
  expect(stored?.key.revoked_at).not.toBeNull();
});

test("Esc com a revogação em voo não fecha; a falha aparece e o diálogo reabre", async ({
  page,
  fakeApi,
}) => {
  await fakeApi.signIn();
  const acesso = new AcessoPage(page);
  await acesso.open();
  await acesso.createKey(["Ler arquivos (files:read)"]);
  await page.getByRole("button", { name: "Já copiei" }).click();
  const [stored] = [...fakeApi.account.keys.values()];
  const publicId = stored?.key.public_id ?? "";
  const held = holdRequest(page, "**/api/v1/api-keys/*", "DELETE");

  const dialog = await acesso.askRevoke(publicId);
  const confirm = dialog.getByRole("button", { name: "Revogar chave" });
  await confirm.click();
  const revoke = await held;
  // O Esc só depois de a tela mostrar o pedido em voo.
  await expect(confirm).toHaveAttribute("aria-disabled", "true");
  await page.keyboard.press("Escape");
  await expect(dialog).toBeVisible();
  await fulfillProblem(revoke, { code: "SERVICE_UNAVAILABLE", status: 503 });

  await expect(acesso.section("Chaves de API").getByRole("alert")).toContainText(
    "O serviço está indisponível",
  );
  await expect(dialog).toBeHidden();
  await expect(await acesso.askRevoke(publicId)).toBeVisible();
});

test("criar chave sem login recente leva ao provedor com reauth=1 e volta ao Acesso", async ({
  page,
  fakeApi,
}) => {
  await fakeApi.signIn({ recentAuth: false });
  const acesso = new AcessoPage(page);
  await acesso.open();

  await acesso.createKey(["Ler arquivos (files:read)"]);
  const prompt = page.getByRole("region", { name: "Entre de novo para confirmar" });
  await expect(prompt).toBeVisible();
  const login = page.waitForRequest((request) => request.url().includes("/auth/login/google"));
  await prompt.getByRole("link", { name: "Entrar com Google" }).click();

  const loginUrl = new URL((await login).url());
  expect(loginUrl.searchParams.get("reauth")).toBe("1");
  expect(loginUrl.searchParams.get("return_to")).toBe("/acesso");
  await expect(page).toHaveURL(/\/acesso$/);
  await expect(await acesso.createKey(["Ler arquivos (files:read)"])).toBeVisible();
});

test("excluir a conta só age com a frase; depois, a exclusão pedida e nenhuma sessão", async ({
  page,
  context,
  fakeApi,
}) => {
  await fakeApi.signIn();
  const acesso = new AcessoPage(page);
  await acesso.open();

  const dialog = await acesso.askDeleteAccount();
  const phrase = dialog.getByLabel("Digite “excluir minha conta”");
  await expect(phrase).toBeFocused();
  const confirm = dialog.getByRole("button", { name: "Excluir conta" });
  await phrase.fill("excluir");
  await expect(confirm).toHaveAttribute("aria-disabled", "true");
  // aria-disabled deixa o botão no Tab; o Playwright o trata como desabilitado, então o clique é
  // forçado para provar que ele não age.
  await confirm.click({ force: true });
  expect(fakeApi.account.deleted).toBe(false);

  await phrase.fill("excluir minha conta");
  await confirm.click();

  await expect(page).toHaveURL(/\/conta-excluida$/);
  await expect(page.getByRole("heading", { level: 1, name: "Exclusão pedida" })).toBeFocused();
  expect(fakeApi.account.deleted).toBe(true);
  expect(await context.cookies()).toEqual([]);
});

test("Esc com a exclusão da conta em voo não fecha; a falha aparece e o diálogo reabre", async ({
  page,
  fakeApi,
}) => {
  await fakeApi.signIn();
  const acesso = new AcessoPage(page);
  await acesso.open();
  const held = holdRequest(page, "**/api/v1/me", "DELETE");

  const dialog = await acesso.askDeleteAccount();
  await dialog.getByLabel("Digite “excluir minha conta”").fill("excluir minha conta");
  await dialog.getByRole("button", { name: "Excluir conta" }).click();
  const deletion = await held;
  // O Esc só depois de a tela mostrar o pedido em voo.
  await expect(dialog.getByRole("button", { name: "Excluindo…" })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(dialog).toBeVisible();
  await fulfillProblem(deletion, { code: "SERVICE_UNAVAILABLE", status: 503 });

  await expect(acesso.section("Excluir conta").getByRole("alert")).toContainText(
    "O serviço está indisponível",
  );
  await expect(dialog).toBeHidden();
  await expect(await acesso.askDeleteAccount()).toBeVisible();
  expect(fakeApi.account.deleted).toBe(false);
});
