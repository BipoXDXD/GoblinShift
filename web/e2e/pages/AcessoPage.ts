import { expect, type Locator, type Page } from "@playwright/test";

/** O Acesso: sessão, uso, chaves de API e exclusão da conta, por ações de intenção. */
export class AcessoPage {
  readonly #page: Page;

  constructor(page: Page) {
    this.#page = page;
  }

  async open(): Promise<void> {
    await this.#page.goto("/acesso");
    await expect(this.#page.getByRole("heading", { level: 1, name: "Acesso" })).toBeVisible();
  }

  section(name: string): Locator {
    return this.#page.getByRole("region", { name });
  }

  async signOut(): Promise<void> {
    await this.section("Sessão").getByRole("button", { name: "Sair", exact: true }).click();
  }

  /** Abre a confirmação de "Sair de todos os dispositivos" e devolve o diálogo. */
  async askSignOutAll(): Promise<Locator> {
    await this.section("Sessão")
      .getByRole("button", { name: "Sair de todos os dispositivos" })
      .click();
    return this.#page.getByRole("dialog", { name: "Sair de todos os dispositivos?" });
  }

  /**
   * Cria uma chave com os escopos pelo rótulo visível; devolve a seção da chave criada. Só volta
   * depois da resposta do POST, qualquer que seja o status, para a spec ler o estado da API falsa
   * (token emitido, chave guardada) já gravado, e não o de antes do clique.
   */
  async createKey(scopeLabels: readonly string[]): Promise<Locator> {
    const keys = this.section("Chaves de API");
    await keys.getByRole("button", { name: "Nova chave" }).click();
    const form = this.#page.getByRole("form", { name: "Nova chave de API" });
    for (const label of scopeLabels) {
      await form.getByLabel(label).check();
    }
    const created = this.#page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        new URL(response.url()).pathname.endsWith("/api-keys"),
    );
    await form.getByRole("button", { name: "Criar chave" }).click();
    await created;
    return this.#page.getByRole("region", { name: /^Chave criada: / });
  }

  keyItems(): Locator {
    return this.#page.getByRole("list", { name: "Chaves de API da conta" }).getByRole("listitem");
  }

  /** Abre a confirmação de revogar a chave pelo id público e devolve o diálogo. */
  async askRevoke(publicId: string): Promise<Locator> {
    await this.#page.getByRole("button", { name: `Revogar a chave ${publicId}` }).click();
    return this.#page.getByRole("dialog", { name: `Revogar a chave ${publicId}?` });
  }

  /** Abre a confirmação da exclusão e devolve o diálogo. */
  async askDeleteAccount(): Promise<Locator> {
    await this.section("Excluir conta").getByRole("button", { name: "Excluir conta…" }).click();
    return this.#page.getByRole("dialog", { name: "Excluir a conta?" });
  }
}
