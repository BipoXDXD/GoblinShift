import { expect, type Download, type Page } from "@playwright/test";

import { E2E } from "../support/env";

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
    const link = this.#page.getByRole("link", { name: new RegExp(`^Baixar ${fileName}`) });
    // O Chromium baixa `a[download]` por fora da interceptação de rede, e a chave do modo remoto só
    // existe nela: sem o atributo, o clique vira a navegação que o Playwright intercepta, e o
    // `Content-Disposition: attachment` da API continua decidindo que é download e o nome.
    if (E2E.remote) {
      await link.evaluate((anchor) => {
        anchor.removeAttribute("download");
      });
    }
    const [download] = await Promise.all([this.#page.waitForEvent("download"), link.click()]);
    return download;
  }
}
