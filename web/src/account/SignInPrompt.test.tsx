import { screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { meBody } from "../test/account";
import { fakeApi, jsonResponse } from "../test/fakeApi";
import { renderRoutes } from "../test/renderRoutes";
import { SignInPrompt } from "./SignInPrompt";

function setup(reauth: boolean) {
  const api = fakeApi({ "GET /api/v1/me": () => jsonResponse(meBody({ provider: "github" })) });
  renderRoutes({
    routes: [{ path: "/acesso", element: <SignInPrompt reauth={reauth} /> }],
    path: "/acesso",
    client: api.client,
  });
  return api;
}

describe("SignInPrompt", () => {
  it("na reautenticação lê o /me e mostra só o provedor da sessão", async () => {
    const api = setup(true);

    // Enquanto o /me carrega, os dois aparecem; com ele, só o da sessão.
    await waitFor(() => {
      expect(screen.queryByRole("link", { name: "Entrar com Google" })).toBeNull();
    });
    expect(screen.getByRole("link", { name: "Entrar com GitHub" })).toHaveAttribute(
      "href",
      "/auth/login/github?return_to=%2Facesso&reauth=1",
    );
    expect(api.requests.map((request) => new URL(request.url).pathname)).toEqual(["/api/v1/me"]);
  });

  it("no login comum mostra os dois provedores sem pedir o /me", () => {
    const api = setup(false);

    expect(screen.getAllByRole("link").map((link) => link.getAttribute("href"))).toEqual([
      "/auth/login/google?return_to=%2Facesso",
      "/auth/login/github?return_to=%2Facesso",
    ]);
    expect(api.requests).toEqual([]);
  });
});
