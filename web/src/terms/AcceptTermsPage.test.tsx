import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { meBody, TERMS_PENDING } from "../test/account";
import { appRoutes } from "../test/appRoutes";
import { axeViolations } from "../test/axe";
import { fakeApi, jsonResponse, problemResponse, screenRequests } from "../test/fakeApi";
import { expectHeadingFocused } from "../test/focus";
import { currentUrl, renderRoutes } from "../test/renderRoutes";

type Handler = (request: Request) => Response | Promise<Response> | null;

const CSRF = "csrf-da-sessao";

function setup(handlers: Readonly<Record<string, Handler>> = {}, path = "/termos/aceitar") {
  const api = fakeApi(
    {
      "GET /api/v1/me": () => jsonResponse(meBody({ terms: TERMS_PENDING })),
      "GET /api/v1/runs": () => jsonResponse({ data: [], next_cursor: null }),
      ...handlers,
    },
    { csrfToken: CSRF },
  );
  const view = renderRoutes({ routes: appRoutes(api.client), path, client: api.client });
  return { ...view, api, user: userEvent.setup() };
}

function acceptCalls(requests: readonly Request[]): Request[] {
  return screenRequests(requests).filter(
    (request) => new URL(request.url).pathname === "/api/v1/me/terms:accept",
  );
}

describe("Aceite dos termos", () => {
  it("carregando o /me", async () => {
    setup({ "GET /api/v1/me": () => null });

    await expectHeadingFocused("Termos de uso");
    expect(screen.getByText("Carregando os termos.")).toBeVisible();
  });

  it("sem sessão, convida a entrar", async () => {
    setup({ "GET /api/v1/me": () => problemResponse(401, "AUTHENTICATION_REQUIRED") });

    expect(await screen.findByRole("region", { name: "Entre para continuar" })).toBeVisible();
  });

  it("mostra a versão vigente e os links para ler os dois textos", async () => {
    setup();

    expect(
      await screen.findByText(
        "Para continuar, leia e aceite a versão v1 dos Termos de Uso e do Aviso de Privacidade.",
      ),
    ).toBeVisible();
    expect(screen.getByRole("link", { name: "Ler os Termos de Uso" })).toHaveAttribute(
      "href",
      "/termos",
    );
    expect(screen.getByRole("link", { name: "Ler o Aviso de Privacidade" })).toHaveAttribute(
      "href",
      "/privacidade",
    );
  });

  it("aceitar manda a versão lida com o CSRF e volta para a tela de antes", async () => {
    let accepted = false;
    const { api, user, router } = setup(
      {
        "GET /api/v1/me": () => jsonResponse(meBody(accepted ? {} : { terms: TERMS_PENDING })),
        "POST /api/v1/me/terms:accept": () => {
          accepted = true;
          return jsonResponse({ version: "v1", accepted_at: "2026-10-14T12:00:00Z" });
        },
      },
      "/termos/aceitar?volta=%2Fhistorico%3Festado%3Dfailed",
    );

    await user.click(await screen.findByRole("button", { name: "Aceitar e continuar" }));

    await waitFor(() => {
      expect(currentUrl(router)).toBe("/historico?estado=failed");
    });
    const [post] = acceptCalls(api.requests);
    expect(await post?.json()).toEqual({ version: "v1" });
    expect(post?.headers.get("X-CSRF-Token")).toBe(CSRF);
    expect(await screen.findByRole("heading", { level: 1, name: "Histórico" })).toBeVisible();
  });

  it("?volta= para fora da origem vira a Oficina", async () => {
    let accepted = false;
    const { user, router } = setup(
      {
        "GET /api/v1/me": () => jsonResponse(meBody(accepted ? {} : { terms: TERMS_PENDING })),
        "POST /api/v1/me/terms:accept": () => {
          accepted = true;
          return jsonResponse({ version: "v1", accepted_at: "2026-10-14T12:00:00Z" });
        },
      },
      "/termos/aceitar?volta=%2F%2Fevil.example",
    );

    await user.click(await screen.findByRole("button", { name: "Aceitar e continuar" }));

    await waitFor(() => {
      expect(currentUrl(router)).toBe("/");
    });
  });

  it("versão que mudou (409) pede para ler a nova e relê o /me", async () => {
    let version = "v1";
    const { api, user } = setup({
      "GET /api/v1/me": () =>
        jsonResponse(meBody({ terms: { ...TERMS_PENDING, current_version: version } })),
      "POST /api/v1/me/terms:accept": () => {
        version = "v2";
        return problemResponse(409, "STATE_CONFLICT");
      },
    });

    await user.click(await screen.findByRole("button", { name: "Aceitar e continuar" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "A versão dos termos mudou enquanto você lia. Leia a versão nova e aceite de novo.",
    );
    expect(await screen.findByText(/aceite a versão v2/)).toBeVisible();
    expect(acceptCalls(api.requests)).toHaveLength(1);
  });

  it("CSRF recusado pede para recarregar", async () => {
    const { user } = setup({
      "POST /api/v1/me/terms:accept": () => problemResponse(403, "CSRF_FAILED"),
    });

    await user.click(await screen.findByRole("button", { name: "Aceitar e continuar" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/Recarregue a página/);
  });

  it("não aceitar é sair", async () => {
    let signedIn = true;
    const { user, router } = setup({
      "GET /api/v1/me": () =>
        signedIn
          ? jsonResponse(meBody({ terms: TERMS_PENDING }))
          : problemResponse(401, "AUTHENTICATION_REQUIRED"),
      "POST /auth/logout": () => {
        signedIn = false;
        return new Response(null, { status: 204 });
      },
    });

    await user.click(await screen.findByRole("button", { name: "Não aceito, sair" }));

    await waitFor(() => {
      expect(currentUrl(router)).toBe("/");
    });
    expect(
      await within(screen.getByRole("navigation", { name: "Principal" })).findByRole("link", {
        name: "Entrar",
      }),
    ).toBeVisible();
  });

  it("quem já aceitou vê a versão aceita e segue", async () => {
    setup({ "GET /api/v1/me": () => jsonResponse(meBody()) }, "/termos/aceitar?volta=%2Facesso");

    expect(await screen.findByText("Você já aceitou a versão v1 dos termos.")).toBeVisible();
    expect(screen.getByRole("link", { name: "Continuar" })).toHaveAttribute("href", "/acesso");
    expect(screen.queryByRole("button", { name: "Aceitar e continuar" })).toBeNull();
  });

  it("não tem violações do axe", async () => {
    const { container } = setup();
    await screen.findByRole("button", { name: "Aceitar e continuar" });

    expect(await axeViolations(container)).toEqual([]);
  });
});
