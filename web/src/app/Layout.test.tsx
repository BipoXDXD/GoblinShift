import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { SiteNotice } from "../notice/notice";
import { meBody, TERMS_PENDING } from "../test/account";
import { appRoutes } from "../test/appRoutes";
import { axeViolations } from "../test/axe";
import { fakeApi, jsonResponse, problemResponse } from "../test/fakeApi";
import { currentUrl, renderRoutes } from "../test/renderRoutes";

type MeHandler = () => Response | null;

function setup(me: MeHandler, path = "/", notice: SiteNotice | null = null) {
  const api = fakeApi({
    "GET /api/v1/me": me,
    "GET /api/v1/runs": () => jsonResponse({ data: [], next_cursor: null }),
  });
  const view = renderRoutes({
    routes: appRoutes(api.client, { notice }),
    path,
    client: api.client,
  });
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

  it.each(["/?login_error=outro", "/?login_error=", "/"])(
    "%s não mostra erro de login",
    async (path) => {
      setup(() => jsonResponse(meBody()), path);

      expect(await screen.findByRole("heading", { level: 1 })).toBeVisible();
      expect(screen.queryByText(/Não foi possível entrar/)).toBeNull();
    },
  );

  it("não tem violações do axe com o erro de login", async () => {
    const { container } = setup(
      () => problemResponse(401, "AUTHENTICATION_REQUIRED"),
      "/?login_error=failed",
    );
    await within(await nav()).findByRole("link", { name: "Entrar" });

    expect(await axeViolations(container)).toEqual([]);
  });
});

describe("Layout: aviso global", () => {
  /** Na janela do relógio fixo dos testes (14/10/2026 12:00 UTC). */
  const INCIDENT: SiteNotice = {
    id: "incidente-2026-10",
    kind: "incident",
    title: "Incidente de segurança em andamento",
    body: "Detalhes no aviso de privacidade.",
    link: { href: "/privacidade", label: "Ler o aviso" },
    fromMs: Date.parse("2026-10-14T00:00:00Z"),
    untilMs: null,
  };

  it.each([
    ["com sessão", () => jsonResponse(meBody())],
    ["sem sessão", () => problemResponse(401, "AUTHENTICATION_REQUIRED")],
  ])("%s, aparece entre a barra e o conteúdo", async (_case, me) => {
    setup(me, "/historico", INCIDENT);
    await nav();

    const notice = screen.getByRole("region", { name: "Aviso: incidente de segurança" });
    const header = screen.getByRole("banner");
    const main = screen.getByRole("main");
    expect(header.compareDocumentPosition(notice)).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
    expect(notice.compareDocumentPosition(main)).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
  });

  it("fora da janela do relógio das rotas, não aparece", async () => {
    setup(() => jsonResponse(meBody()), "/historico", {
      ...INCIDENT,
      fromMs: Date.parse("2026-10-14T12:00:00.001Z"),
    });
    await nav();

    expect(screen.queryByRole("region", { name: /^Aviso/ })).toBeNull();
  });

  it("sem aviso no build, não aparece", async () => {
    setup(() => jsonResponse(meBody()), "/historico");
    await nav();

    expect(screen.queryByRole("region", { name: /^Aviso/ })).toBeNull();
  });

  it("não tem violações do axe com o aviso na moldura", async () => {
    const { container } = setup(() => jsonResponse(meBody()), "/historico", INCIDENT);
    await screen.findByRole("heading", { level: 1, name: "Histórico" });

    expect(await axeViolations(container)).toEqual([]);
  });
});
