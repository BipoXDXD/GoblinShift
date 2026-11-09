import { screen } from "@testing-library/react";
import { describe, expect, it, vi, type MockInstance } from "vitest";

import { appRoutes } from "../test/appRoutes";
import { fakeApi, jsonResponse } from "../test/fakeApi";
import { meBody } from "../test/account";
import { renderRoutes } from "../test/renderRoutes";
import { TEXT } from "../text";

const { CHUNK_CANARY, RENDER_CANARY } = vi.hoisted(() => ({
  CHUNK_CANARY: "CANARY-chunk-ausente",
  RENDER_CANARY: "CANARY-defeito-da-tela",
}));
const LOG_LABEL = "Falha ao abrir a tela:";
/** Trecho de toda linha da stack destes erros: o caminho deste arquivo. */
const STACK_FRAME = "routeFailure.test";

// O chunk da tela não vem: é o que acontece numa aba aberta depois de uma versão nova ser publicada.
// O getter rejeita o `import()` com o próprio erro; um `throw` na factory o Vitest embrulharia.
vi.mock("../history/HistoryPage", () => ({
  get HistoryPage(): never {
    throw new Error(CHUNK_CANARY);
  },
}));

// A tela quebra ao renderizar.
vi.mock("../legal/CreditsPage", () => ({
  CreditsPage: () => {
    throw new Error(RENDER_CANARY);
  },
}));

const SCREENS = [
  { path: "/historico", canary: CHUNK_CANARY },
  { path: "/creditos", canary: RENDER_CANARY },
];

function setup(path: string): MockInstance<typeof console.error> {
  // O React e o router registram o erro no console; aqui ele é o cenário, não um defeito.
  const consoleError = vi.spyOn(console, "error").mockImplementation(() => undefined);
  vi.spyOn(console, "warn").mockImplementation(() => undefined);
  const api = fakeApi({ "GET /api/v1/me": () => jsonResponse(meBody()) });
  renderRoutes({ routes: appRoutes(api.client), path, client: api.client });
  return consoleError;
}

describe("falha ao abrir uma tela", () => {
  it.each(SCREENS)(
    "$path mostra só o aviso próprio, na moldura",
    async ({ path, canary }) => {
      setup(path);

      const alert = await screen.findByRole("alert");

      expect(alert.textContent).toBe(TEXT.failure.internal);
      expect(screen.getByRole("navigation", { name: TEXT.layout.navLabel })).toBeInTheDocument();
      expect(screen.queryByText("Unexpected Application Error!")).not.toBeInTheDocument();
      expect(document.body.textContent).not.toContain(canary);
      expect(document.body.textContent).not.toContain(STACK_FRAME);
    },
    10_000,
  );

  it.each(SCREENS)(
    "$path registra o erro no console",
    async ({ path, canary }) => {
      const consoleError = setup(path);

      await screen.findByRole("alert");

      expect(consoleError).toHaveBeenCalledWith(
        LOG_LABEL,
        expect.objectContaining({ message: canary }),
      );
    },
    10_000,
  );
});
