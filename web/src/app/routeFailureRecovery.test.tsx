import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { appRoutes } from "../test/appRoutes";
import { fakeApi, jsonResponse } from "../test/fakeApi";
import { meBody } from "../test/account";
import { expectHeadingFocused } from "../test/focus";
import { renderRoutes } from "../test/renderRoutes";
import { reloadPage } from "./reloadPage";

const broken = vi.hoisted(() => ({ credits: true }));

// Créditos quebra no primeiro render e abre depois: um defeito passageiro, como um dado ruim.
vi.mock("../legal/CreditsPage", () => ({
  CreditsPage: () => {
    if (broken.credits) {
      throw new Error("CANARY-defeito-passageiro");
    }
    return <p>Créditos abertos</p>;
  },
}));

// O jsdom não deixa espiar o location.reload (propriedade não configurável).
vi.mock("./reloadPage", () => ({ reloadPage: vi.fn() }));

function setup() {
  broken.credits = true;
  vi.spyOn(console, "error").mockImplementation(() => undefined);
  vi.spyOn(console, "warn").mockImplementation(() => undefined);
  const api = fakeApi({ "GET /api/v1/me": () => jsonResponse(meBody()) });
  return {
    ...renderRoutes({ routes: appRoutes(api.client), path: "/creditos", client: api.client }),
    user: userEvent.setup(),
  };
}

describe("saída da tela que não abriu", () => {
  it("a mesma rota abrindo depois devolve o título da aba da tela", async () => {
    const { router } = setup();
    await expectHeadingFocused("Não foi possível abrir esta tela");
    await waitFor(() => {
      expect(document.title).toBe("Erro ao abrir a tela · GoblinShift");
    });

    broken.credits = false;
    await act(() => router.navigate("/creditos"));

    expect(await screen.findByText("Créditos abertos")).toBeVisible();
    expect(document.title).toBe("Créditos · GoblinShift");
  });

  it("a mesma rota falhando de novo mantém o título de erro", async () => {
    const { router } = setup();
    await expectHeadingFocused("Não foi possível abrir esta tela");

    await act(() => router.navigate("/creditos"));

    await waitFor(() => {
      expect(document.title).toBe("Erro ao abrir a tela · GoblinShift");
    });
    expect(screen.queryByText("Créditos abertos")).toBeNull();
  });

  it("Recarregar a página recarrega a página", async () => {
    const { user } = setup();
    await expectHeadingFocused("Não foi possível abrir esta tela");

    await user.click(screen.getByRole("button", { name: "Recarregar a página" }));

    expect(reloadPage).toHaveBeenCalledOnce();
  });
});
