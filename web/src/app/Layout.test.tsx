import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { meBody, TERMS_PENDING } from "../test/account";
import { appRoutes } from "../test/appRoutes";
import { axeViolations } from "../test/axe";
import { fakeApi, jsonResponse, problemResponse } from "../test/fakeApi";
import { currentUrl, renderRoutes } from "../test/renderRoutes";

type MeHandler = () => Response | null;

const REF = "0b8f3c2e-5d4a-4f1b-9c7e-2a6d8e0f1b3c";

function setup(me: MeHandler, path = "/") {
  const api = fakeApi({
    "GET /api/v1/me": me,
    "GET /api/v1/runs": () => jsonResponse({ data: [], next_cursor: null }),
  });
  const view = renderRoutes({ routes: appRoutes(api.client), path, client: api.client });
  return { ...view, api };
}

/** O cabeçalho só aparece depois do chunk da primeira tela (rotas lazy). */
function nav(): Promise<HTMLElement> {
  return screen.findByRole("navigation", { name: "Principal" });
}

describe("Layout: conta no cabeçalho", () => {
  it("com sessão, o link é Acesso", async () => {
    setup(() => jsonResponse(meBody()));

    expect(await within(await nav()).findByRole("link", { name: "Acesso" })).toHaveAttribute(
      "href",
      "/acesso",
    );
  });

  it("sem sessão, o mesmo lugar vira Entrar, voltando para a tela atual", async () => {
    setup(() => problemResponse(401, "AUTHENTICATION_REQUIRED"), "/historico?estado=failed");

    expect(await within(await nav()).findByRole("link", { name: "Entrar" })).toHaveAttribute(
      "href",
      "/entrar?volta=%2Fhistorico%3Festado%3Dfailed",
    );
    expect(within(await nav()).queryByRole("link", { name: "Acesso" })).toBeNull();
  });

  it("enquanto o /me carrega, mostra Acesso (sem pular quando a resposta chega)", async () => {
    setup(() => null);

    expect(await within(await nav()).findByRole("link", { name: "Acesso" })).toBeVisible();
  });

  it("outra falha do /me não esconde a navegação", async () => {
    setup(() => problemResponse(503, "SERVICE_UNAVAILABLE"));

    expect(await within(await nav()).findByRole("link", { name: "Acesso" })).toBeVisible();
    expect(within(await nav()).getByRole("link", { name: "Histórico" })).toBeVisible();
  });
});

describe("Layout: termos pendentes", () => {
  it("leva ao aceite antes de qualquer tela, guardando a volta", async () => {
    const { router } = setup(
      () => jsonResponse(meBody({ terms: TERMS_PENDING })),
      "/historico?estado=failed",
    );

    await waitFor(() => {
      expect(currentUrl(router)).toBe("/termos/aceitar?volta=%2Fhistorico%3Festado%3Dfailed");
    });
    expect(await screen.findByRole("heading", { level: 1, name: "Termos de uso" })).toBeVisible();
  });

  it.each(["/termos", "/privacidade", "/acesso", "/entrar"])(
    "%s continua aberta para ler, sair ou excluir a conta",
    async (path) => {
      const { router } = setup(() => jsonResponse(meBody({ terms: TERMS_PENDING })), path);

      expect(await screen.findByRole("heading", { level: 1 })).toBeVisible();
      expect(currentUrl(router)).toBe(path);
    },
  );

  it("com os termos aceitos, a tela abre normalmente", async () => {
    const { router } = setup(() => jsonResponse(meBody()), "/historico");

    expect(await screen.findByRole("heading", { level: 1, name: "Histórico" })).toBeVisible();
    expect(currentUrl(router)).toBe("/historico");
  });
});

describe("Layout: login recusado", () => {
  it("?login_error=failed mostra o erro literal, sem o motivo", async () => {
    setup(() => problemResponse(401, "AUTHENTICATION_REQUIRED"), "/?login_error=failed");

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Não foi possível entrar. Tente de novo; se continuar, confira se a conta do provedor está liberada.",
    );
  });

  it("mostra o código de referência do fragmento, para citar no recurso", async () => {
    setup(() => problemResponse(401, "AUTHENTICATION_REQUIRED"), `/?login_error=failed#ref=${REF}`);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/^Não foi possível entrar\./);
    expect(within(alert).getByText(`Código do pedido para suporte: ${REF}`)).toBeVisible();
  });

  it.each([
    ["texto injetado", "#ref=Escreva%20para%20golpe%40example.com"],
    ["HTML", "#ref=%3Cb%3EPERIGO%3C%2Fb%3E"],
    ["quase um UUID", `#ref=${REF}0`],
    ["sem código", ""],
  ])("código inválido (%s) não aparece, e o erro continua", async (_name, hash) => {
    setup(() => problemResponse(401, "AUTHENTICATION_REQUIRED"), `/?login_error=failed${hash}`);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(
      "Não foi possível entrar. Tente de novo; se continuar, confira se a conta do provedor está liberada.",
    );
    expect(within(alert).queryByText(/Código/)).toBeNull();
    expect(alert).not.toHaveTextContent(/golpe|PERIGO|<b>/);
  });

  it("código sem o login_error não mostra nada", async () => {
    setup(() => jsonResponse(meBody()), `/#ref=${REF}`);

    expect(await screen.findByRole("heading", { level: 1 })).toBeVisible();
    expect(screen.queryByText(new RegExp(REF))).toBeNull();
  });

  it.each(["/?login_error=outro", "/?login_error=", "/"])(
    "%s não mostra erro de login",
    async (path) => {
      setup(() => jsonResponse(meBody()), path);

      expect(await screen.findByRole("heading", { level: 1 })).toBeVisible();
      expect(screen.queryByText(/Não foi possível entrar/)).toBeNull();
    },
  );

  it("não tem violações do axe com o erro de login e o código", async () => {
    const { container } = setup(
      () => problemResponse(401, "AUTHENTICATION_REQUIRED"),
      `/?login_error=failed#ref=${REF}`,
    );
    await within(await nav()).findByRole("link", { name: "Entrar" });

    expect(await axeViolations(container)).toEqual([]);
  });
});
