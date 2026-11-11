// Tela que não abriu (WCAG 1.3.1, 2.4.2 e 2.4.6): o pedaço do Histórico não chega, e a pessoa recebe
// um título com foco, a aba dizendo que houve erro e uma saída que funciona.
import { expect, test } from "../support/test";

const JS_FILE = /\.js(\?.*)?$/;

test("pedaço da tela que não carrega mostra o erro com foco e recarregar abre a tela", async ({
  page,
}) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  // O bundle principal já rodou; daqui em diante só o `lazy` do Histórico falha.
  await page.route(JS_FILE, (route) => route.abort());

  await page
    .getByRole("navigation", { name: "Principal" })
    .getByRole("link", { name: "Histórico" })
    .click();

  await expect(
    page.getByRole("heading", { level: 1, name: "Não foi possível abrir esta tela" }),
  ).toBeFocused();
  await expect(page).toHaveTitle("Erro ao abrir a tela · GoblinShift");
  await expect(page.getByRole("link", { name: "Voltar à Oficina" })).toHaveAttribute("href", "/");

  await page.unroute(JS_FILE);
  await page.getByRole("button", { name: "Recarregar a página" }).click();

  await expect(page.getByRole("heading", { level: 1, name: "Histórico" })).toBeVisible();
  await expect(page).toHaveTitle("Histórico · GoblinShift");
});
