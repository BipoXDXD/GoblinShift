import { expect, type Locator, type Page } from "@playwright/test";

/** O Histórico: a lista de execuções, os filtros na URL e o "Carregar mais". */
export class HistoricoPage {
  readonly #page: Page;

  constructor(page: Page) {
    this.#page = page;
  }

  /** Abre com os parâmetros da tela (`estado`, `receita`, `periodo`) e espera o título. */
  async open(search = ""): Promise<void> {
    await this.#page.goto(`/historico${search}`);
    await expect(this.#page.getByRole("heading", { level: 1, name: "Histórico" })).toBeVisible();
  }

  /** As linhas da lista, uma por execução. */
  rows(): Locator {
    return this.#page.getByRole("list", { name: "Execuções" }).getByRole("listitem");
  }

  async loadMore(): Promise<void> {
    await this.#page.getByRole("button", { name: "Carregar mais" }).click();
  }

  /** Abre os filtros, escolhe o estado pelo rótulo visível e aplica. */
  async filterByStatus(label: string): Promise<void> {
    const filters = this.#page.getByText("Filtros", { exact: true });
    if ((await this.#page.locator("details[open]").count()) === 0) {
      await filters.click();
    }
    await this.#page.getByLabel("Estado", { exact: true }).selectOption({ label });
    await this.#page.getByRole("button", { name: "Aplicar filtros" }).click();
  }
}
