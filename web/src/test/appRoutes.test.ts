import { describe, expect, it } from "vitest";

import { preloadRoutes } from "./appRoutes";

describe("preloadRoutes", () => {
  it("carrega a tela de um caminho com parâmetro", async () => {
    await expect(preloadRoutes("/execucoes/qualquer-id")).resolves.toBeUndefined();
  });

  it("falha alto com um caminho que só a página não encontrada atende", async () => {
    await expect(preloadRoutes("/rota-que-nao-existe")).rejects.toThrow(
      "Nenhuma tela lazy responde por /rota-que-nao-existe.",
    );
  });
});
