import { screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { appRoutes } from "../test/appRoutes";
import { fakeApi, jsonResponse } from "../test/fakeApi";
import { meBody } from "../test/account";
import { renderRoutes } from "../test/renderRoutes";
import { TEXT } from "../text";

// O chunk da tela não vem: é o que acontece numa aba aberta depois de uma versão nova ser publicada.
vi.mock("../history/HistoryPage", () => {
  throw new Error("chunk ausente");
});

// A tela quebra ao renderizar.
vi.mock("../legal/CreditsPage", () => ({
  CreditsPage: () => {
    throw new Error("defeito da tela");
  },
}));

function setup(path: string): void {
  const api = fakeApi({ "GET /api/v1/me": () => jsonResponse(meBody()) });
  renderRoutes({ routes: appRoutes(api.client), path, client: api.client });
}

describe("falha ao abrir uma tela", () => {
  it.each(["/historico", "/creditos"])(
    "%s mostra o aviso próprio, na moldura",
    async (path) => {
      // O React e o router registram o erro no console; aqui ele é o cenário, não um defeito.
      vi.spyOn(console, "error").mockImplementation(() => undefined);
      vi.spyOn(console, "warn").mockImplementation(() => undefined);
      setup(path);

      expect(await screen.findByRole("alert")).toHaveTextContent(TEXT.failure.internal);
      expect(screen.getByRole("navigation", { name: TEXT.layout.navLabel })).toBeInTheDocument();
      expect(screen.queryByText("Unexpected Application Error!")).not.toBeInTheDocument();
    },
    10_000,
  );
});
