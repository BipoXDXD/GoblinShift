import { screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { appRoutes } from "../test/appRoutes";
import { fakeApi, jsonResponse } from "../test/fakeApi";
import { meBody } from "../test/account";
import { axeViolations } from "../test/axe";
import { allowConsole, consoleCalls } from "../test/consoleGuard";
import { expectHeadingFocused } from "../test/focus";
import { renderRoutes } from "../test/renderRoutes";
import { TEXT } from "../text";

const { CHUNK_CANARY, RENDER_CANARY, SECRET_CANARY } = vi.hoisted(() => ({
  CHUNK_CANARY: "CANARY-chunk-ausente",
  RENDER_CANARY: "CANARY-defeito-da-tela",
  SECRET_CANARY: "CANARY-segredo-do-erro",
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

// A tela quebra ao renderizar, com um erro que carrega um dado que não pode ir ao console.
vi.mock("../legal/CreditsPage", () => ({
  CreditsPage: () => {
    throw Object.assign(new Error(RENDER_CANARY), { segredo: SECRET_CANARY });
  },
}));

const SCREENS = [
  { path: "/historico", canary: CHUNK_CANARY },
  { path: "/creditos", canary: RENDER_CANARY },
];

function setup(path: string, canary: string): void {
  // O React, o router e o RouteFailure registram o erro no console; aqui ele é o cenário, não um
  // defeito. O chunk ausente não passa pelo React: o canário casa só com o registro do RouteFailure.
  allowConsole("error", /Falha ao abrir a tela:/, "o RouteFailure registra a falha: é o cenário");
  allowConsole("error", new RegExp(canary), "o React e o router registram o erro: é o cenário");
  const api = fakeApi({ "GET /api/v1/me": () => jsonResponse(meBody()) });
  renderRoutes({ routes: appRoutes(api.client), path, client: api.client });
}

/** O que o RouteFailure registrou com o rótulo, como unknown: os parâmetros do console são any[]. */
function loggedFailures(): unknown[] {
  return consoleCalls()
    .filter(
      (call) => call.method === "error" && call.args.length === 2 && call.args[0] === LOG_LABEL,
    )
    .map((call) => call.args[1]);
}

describe("falha ao abrir uma tela", () => {
  it.each(SCREENS)(
    "$path mostra só o aviso próprio, na moldura",
    async ({ path, canary }) => {
      setup(path, canary);

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
    "$path tem título com foco, a ação de recarregar e o caminho de volta",
    async ({ path, canary }) => {
      setup(path, canary);

      await expectHeadingFocused("Não foi possível abrir esta tela");
      expect(screen.getByRole("button", { name: "Recarregar a página" })).toHaveAttribute(
        "type",
        "button",
      );
      expect(screen.getByRole("link", { name: "Voltar à Oficina" })).toHaveAttribute("href", "/");
      expect(await axeViolations(document.body)).toEqual([]);
    },
    10_000,
  );

  it.each(SCREENS)(
    "$path troca o título da aba pelo do erro",
    async ({ path, canary }) => {
      document.title = "Título anterior";
      setup(path, canary);

      await screen.findByRole("alert");

      // O título da rota (Histórico, Créditos) diria que a tela abriu.
      await waitFor(() => {
        expect(document.title).toBe("Erro ao abrir a tela · GoblinShift");
      });
    },
    10_000,
  );

  it.each(SCREENS)(
    "$path registra o erro no console",
    async ({ path, canary }) => {
      setup(path, canary);

      await screen.findByRole("alert");

      // O registro sai num efeito, que pode rodar depois de o aviso aparecer.
      await waitFor(() => {
        expect(loggedFailures()).toContainEqual(expect.objectContaining({ message: canary }));
      });
    },
    10_000,
  );

  it("/creditos registra só os campos permitidos do erro, sem o próprio erro", async () => {
    setup("/creditos", RENDER_CANARY);

    await screen.findByRole("alert");

    await waitFor(() => {
      expect(loggedFailures().length).toBeGreaterThan(0);
    });
    for (const value of loggedFailures()) {
      expect(value).not.toBeInstanceOf(Error);
      expect(JSON.stringify(value)).not.toContain(SECRET_CANARY);
    }
  }, 10_000);
});
