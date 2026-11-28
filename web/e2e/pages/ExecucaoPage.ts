import { expect, type Download, type Page } from "@playwright/test";

/** A tela da execução: acompanhar o estado e baixar os resultados. */
export class ExecucaoPage {
  readonly #page: Page;

  constructor(page: Page) {
    this.#page = page;
  }

  async open(runId: string): Promise<void> {
    await this.#page.goto(`/execucoes/${runId}`);
    await expect(this.#page.getByRole("heading", { level: 1, name: "Execução" })).toBeVisible();
  }

  /** Espera o selo de estado mostrar o texto, por exemplo "Concluída". */
  async waitForStatus(status: string): Promise<void> {
    // O polling respeita o Retry-After (2 s): duas consultas cabem com folga.
    await expect(this.#page.getByText(status, { exact: true })).toBeVisible({ timeout: 10_000 });
  }

  /** Imagens de preview das saídas, já carregadas e decodificadas, pelo texto alternativo. */
  async imagePreviews(): Promise<string[]> {
    const previews = this.#page.getByRole("img", { name: /^Pré-visualização de / });
    await expect(previews.first()).toBeVisible();
    return previews.evaluateAll((images) =>
      images
        .filter((image) => image instanceof HTMLImageElement && image.naturalWidth > 0)
        .map((image) => image.getAttribute("alt") ?? ""),
    );
  }

  /** Baixa pelo link "Baixar <arquivo>" e devolve o download já concluído. */
  async download(fileName: string): Promise<Download> {
    // O link não tem o atributo `download`: o clique é a navegação que o Playwright intercepta (e o
    // modo remoto põe a chave nela), e o `Content-Disposition: attachment` da API decide o nome.
    const link = this.#page.getByRole("link", { name: new RegExp(`^Baixar ${fileName}`) });
    const [download] = await Promise.all([this.#page.waitForEvent("download"), link.click()]);
    return download;
  }
}
