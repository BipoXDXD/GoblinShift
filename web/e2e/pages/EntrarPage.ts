import { expect, type Page } from "@playwright/test";

import type { Provider } from "../../src/account/signIn";

const PROVIDER_LABELS: Record<Provider, string> = {
  google: "Entrar com Google",
  github: "Entrar com GitHub",
};

/** Entrar: o cabeçalho leva para cá, e o provedor leva ao login do servidor. */
export class EntrarPage {
  readonly #page: Page;

  constructor(page: Page) {
    this.#page = page;
  }

  /** Pelo link Entrar do cabeçalho, que guarda a tela atual para a volta. */
  async openFromHeader(): Promise<void> {
    await this.#page
      .getByRole("navigation", { name: "Principal" })
      .getByRole("link", { name: "Entrar" })
      .click();
    await expect(
      this.#page.getByRole("heading", { level: 1, name: "Entrar no GoblinShift" }),
    ).toBeVisible();
  }

  async signInWith(provider: Provider): Promise<void> {
    await this.#page
      .getByRole("main")
      .getByRole("link", { name: PROVIDER_LABELS[provider] })
      .click();
  }
}
