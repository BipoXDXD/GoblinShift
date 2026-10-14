// Troca de tema (P2): Sistema, Claro e Escuro mudam o data-theme, a escolha persiste no reload e a
// primeira pintura já sai no tema certo (theme-init.js, antes do CSS e do bundle).
import type { Page } from "@playwright/test";

import { expect, test } from "../support/test";

type Option = "Sistema" | "Claro" | "Escuro";

async function chooseTheme(page: Page, option: Option): Promise<void> {
  // O resumo do <details> abre e fecha o grupo de radios.
  const summary = page.getByText("Tema", { exact: true });
  await summary.click();
  await page.getByRole("radio", { name: option, exact: true }).check();
  await summary.click();
}

const html = (page: Page) => page.locator("html");

test("cada opção do seletor muda o data-theme na hora", async ({ page }) => {
  // Sistema escuro: "Claro" só passa se a escolha vencer a preferência do sistema.
  await page.emulateMedia({ colorScheme: "dark" });
  await page.goto("/");
  await expect(html(page)).toHaveAttribute("data-theme", "dark");

  await chooseTheme(page, "Claro");
  await expect(html(page)).toHaveAttribute("data-theme", "light");
  await chooseTheme(page, "Escuro");
  await expect(html(page)).toHaveAttribute("data-theme", "dark");
  await page.emulateMedia({ colorScheme: "light" });
  await chooseTheme(page, "Sistema");
  await expect(html(page)).toHaveAttribute("data-theme", "light");
});

test("em Sistema, o tema acompanha a troca do sistema com a página aberta", async ({ page }) => {
  await page.emulateMedia({ colorScheme: "light" });
  await page.goto("/");
  await expect(html(page)).toHaveAttribute("data-theme", "light");

  await page.emulateMedia({ colorScheme: "dark" });
  await expect(html(page)).toHaveAttribute("data-theme", "dark");
});

test("a escolha persiste no reload e numa navegação nova", async ({ page }) => {
  await page.emulateMedia({ colorScheme: "light" });
  await page.goto("/");
  await chooseTheme(page, "Escuro");

  await page.reload();
  await expect(html(page)).toHaveAttribute("data-theme", "dark");
  await page.getByText("Tema", { exact: true }).click();
  await expect(page.getByRole("radio", { name: "Escuro", exact: true })).toBeChecked();

  await page.goto("/bancada/csv-janitor");
  await expect(html(page)).toHaveAttribute("data-theme", "dark");
});

for (const { stored, system, expected } of [
  { stored: "dark", system: "light", expected: "dark" },
  { stored: "light", system: "dark", expected: "light" },
  { stored: null, system: "dark", expected: "dark" },
] as const) {
  test(`primeiro quadro já no tema ${expected} (preferência ${stored ?? "nenhuma"}, sistema ${system})`, async ({
    page,
  }) => {
    await page.emulateMedia({ colorScheme: system });
    await page.addInitScript((preference) => {
      if (preference !== null) {
        localStorage.setItem("gs-theme", preference);
      }
      // O primeiro requestAnimationFrame roda antes da primeira pintura, e só depois que o CSS do
      // <head> carregou: o que ele lê é o que a pessoa vê no primeiro quadro.
      requestAnimationFrame(() => {
        const root = document.documentElement;
        root.dataset["firstFrameTheme"] = root.dataset["theme"] ?? "nenhum";
        root.dataset["firstFrameBackground"] = getComputedStyle(root).backgroundColor;
      });
    }, stored);
    await page.goto("/");
    await expect(html(page)).toHaveAttribute("data-first-frame-theme", expected);

    // E o fundo pintado é o do tema final, não o de um tema trocado depois pelo bundle.
    const finalBackground = await html(page).evaluate(
      (root) => getComputedStyle(root).backgroundColor,
    );
    await expect(html(page)).toHaveAttribute("data-first-frame-background", finalBackground);
  });
}
