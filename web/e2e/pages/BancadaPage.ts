import { expect, type Locator, type Page } from "@playwright/test";

import type { InputFile } from "../fixtures/files";

const MOVE_UP = /^Mover para cima /;

export type RecipeId =
  "csv-janitor" | "image-forge" | "downloads-detox" | "pdf-press" | "media-mill";

/**
 * A Bancada de uma receita, por ações de intenção. Os seletores são só papel e nome acessível: se
 * um elemento não se acha assim, isso já é um defeito de acessibilidade (plano da fatia 7, 4.1).
 */
export class BancadaPage {
  readonly #page: Page;

  constructor(page: Page) {
    this.#page = page;
  }

  async open(recipeId: RecipeId): Promise<void> {
    await this.#page.goto(`/bancada/${recipeId}`);
    await expect(this.#page.getByRole("heading", { level: 1, name: /^Bancada:/ })).toBeVisible();
    await expect(this.runButton()).toBeVisible();
  }

  /**
   * Escolhe os arquivos pelo input e espera cada um terminar de enviar. Conta por nome: com um nome
   * repetido, o item antigo já "Enviado" não pode responder pelo novo.
   */
  async addFiles(files: readonly InputFile[]): Promise<void> {
    const expected = new Map<string, number>();
    for (const file of files) {
      expected.set(file.name, (expected.get(file.name) ?? 0) + 1);
    }
    for (const [name, added] of expected) {
      expected.set(name, added + (await this.entry(name).count()));
    }
    await this.#page.getByLabel("Escolher arquivos").setInputFiles([...files]);
    for (const [name, total] of expected) {
      await expect(this.entry(name).getByText("Enviado", { exact: true })).toHaveCount(total);
    }
  }

  /** Remove um arquivo pelo nome, confirmando no diálogo. */
  async removeFile(name: string): Promise<void> {
    await this.#page.getByRole("button", { name: `Remover ${name}` }).click();
    const dialog = this.#page.getByRole("dialog", { name: `Remover ${name}?` });
    await dialog.getByRole("button", { name: "Remover", exact: true }).click();
    await expect(dialog).toBeHidden();
  }

  /** Remove todos os arquivos, confirmando no diálogo com a contagem. */
  async removeAll(): Promise<void> {
    await this.#page.getByRole("button", { name: "Remover todos" }).click();
    const dialog = this.#page.getByRole("dialog", { name: /^Remover os \d+ arquivos\?$/ });
    await dialog.getByRole("button", { name: "Remover todos" }).click();
    await expect(dialog).toBeHidden();
  }

  /** Nomes dos arquivos na ordem de processamento, como a lista mostra. */
  async order(): Promise<string[]> {
    // Cada item tem um "Mover para cima <nome>": o nome acessível já diz a ordem, sem CSS.
    const buttons = this.orderList().getByRole("button", { name: MOVE_UP });
    const labels = await buttons.evaluateAll((elements) =>
      elements.map((element) => element.getAttribute("aria-label") ?? ""),
    );
    return labels.map((label) => label.replace(MOVE_UP, ""));
  }

  /** Reordena só pelos botões (alternativa ao arrasto, WCAG 2.5.7). */
  async moveUp(name: string): Promise<void> {
    await this.#page.getByRole("button", { name: `Mover para cima ${name}` }).click();
  }

  async moveDown(name: string): Promise<void> {
    await this.#page.getByRole("button", { name: `Mover para baixo ${name}` }).click();
  }

  /** Escolhe uma opção de um campo de escolha única pelo rótulo visível. */
  async choose(field: string, option: string): Promise<void> {
    await this.#page.getByLabel(field, { exact: true }).selectOption({ label: option });
  }

  /** Aciona um botão do formulário pelo nome acessível (Adicionar página, no PDF Press). */
  async press(button: string): Promise<void> {
    await this.#page.getByRole("button", { name: button, exact: true }).click();
  }

  /** Marca uma opção de um grupo de radios pelo rótulo visível. */
  async pick(option: string): Promise<void> {
    await this.#page.getByRole("radio", { name: option, exact: true }).check();
  }

  async fill(field: string, value: string): Promise<void> {
    await this.#page.getByLabel(field, { exact: true }).fill(value);
  }

  async check(field: string): Promise<void> {
    await this.#page.getByLabel(field, { exact: true }).check();
  }

  /** Executa e espera a tela da execução. */
  async run(): Promise<void> {
    await this.runButton().click();
    await this.#page.waitForURL(/\/execucoes\/[0-9a-f-]{36}$/);
  }

  runButton(): Locator {
    return this.#page.getByRole("button", { name: "Executar" });
  }

  entry(name: string): Locator {
    return this.orderList().getByRole("listitem").filter({ hasText: name });
  }

  private orderList(): Locator {
    return this.#page.getByRole("list", { name: "Ordem de processamento" });
  }
}
