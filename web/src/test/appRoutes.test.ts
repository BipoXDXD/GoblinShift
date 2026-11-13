import { describe, expect, it, vi } from "vitest";

import { preloadRoutes } from "./appRoutes";

// Marca quando o módulo da tela de execução é avaliado, sem trocar o que ele exporta.
const screenModule = vi.hoisted(() => ({ loaded: false }));
vi.mock("../runs/RunPage", async (importOriginal) => {
  screenModule.loaded = true;
  return importOriginal();
});

describe("preloadRoutes", () => {
  it("carrega a tela de um caminho com parâmetro", async () => {
    expect(screenModule.loaded).toBe(false);

    await preloadRoutes("/execucoes/qualquer-id");

    expect(screenModule.loaded).toBe(true);
  });

  it("falha alto com um caminho que só a página não encontrada atende", async () => {
    await expect(preloadRoutes("/rota-que-nao-existe")).rejects.toThrow(
      "Nenhuma tela lazy responde por /rota-que-nao-existe.",
    );
  });
});
