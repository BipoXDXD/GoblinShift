import { screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { appRoutes } from "../test/appRoutes";
import { fakeApi, jsonResponse } from "../test/fakeApi";
import { meBody } from "../test/account";
import { expectHeadingFocused } from "../test/focus";
import { renderRoutes } from "../test/renderRoutes";
import { TEXT } from "../text";

const { CANARY } = vi.hoisted(() => ({ CANARY: "CANARY-defeito-da-moldura" }));
const STACK_FRAME = "rootFailure.test";

// A moldura quebra: não há moldura em que mostrar o aviso.
vi.mock("../notice/SiteNoticeBanner", () => ({
  SiteNoticeBanner: () => {
    throw new Error(CANARY);
  },
}));

describe("falha na própria moldura", () => {
  it("tem título com foco, a ação de recarregar e o título da aba do erro", async () => {
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    vi.spyOn(console, "warn").mockImplementation(() => undefined);
    document.title = "Título anterior";
    const api = fakeApi({ "GET /api/v1/me": () => jsonResponse(meBody()) });

    renderRoutes({ routes: appRoutes(api.client), path: "/creditos", client: api.client });

    await expectHeadingFocused("Não foi possível abrir esta tela");
    expect(screen.getByRole("button", { name: "Recarregar a página" })).toBeVisible();
    expect(screen.getByRole("link", { name: "Voltar à Oficina" })).toHaveAttribute("href", "/");
    await waitFor(() => {
      expect(document.title).toBe("Erro ao abrir a tela · GoblinShift");
    });
  });

  it("mostra só o aviso próprio e registra o erro no console", async () => {
    // O React e o router registram o erro no console; aqui ele é o cenário, não um defeito.
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => undefined);
    vi.spyOn(console, "warn").mockImplementation(() => undefined);
    const api = fakeApi({ "GET /api/v1/me": () => jsonResponse(meBody()) });

    renderRoutes({ routes: appRoutes(api.client), path: "/creditos", client: api.client });
    const alert = await screen.findByRole("alert");

    expect(alert.textContent).toBe(TEXT.failure.internal);
    expect(screen.getByRole("main")).toContainElement(alert);
    expect(screen.queryByText("Unexpected Application Error!")).not.toBeInTheDocument();
    expect(document.body.textContent).not.toContain(CANARY);
    expect(document.body.textContent).not.toContain(STACK_FRAME);
    // O registro sai num efeito, que pode rodar depois de o aviso aparecer.
    await waitFor(() => {
      expect(consoleError).toHaveBeenCalledWith(
        "Falha ao abrir a tela:",
        expect.objectContaining({ message: CANARY }),
      );
    });
  });
});
