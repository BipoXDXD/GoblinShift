import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { meBody } from "../test/account";
import { appRoutes } from "../test/appRoutes";
import { axeViolations } from "../test/axe";
import { fakeApi, jsonResponse, problemResponse, screenRequests } from "../test/fakeApi";
import { expectHeadingFocused } from "../test/focus";
import { renderRoutes } from "../test/renderRoutes";

function setup(
  path: string,
  me: () => Response = () => problemResponse(401, "AUTHENTICATION_REQUIRED"),
) {
  const api = fakeApi({ "GET /api/v1/me": me });
  const view = renderRoutes({ routes: appRoutes(api.client), path, client: api.client });
  return { ...view, api };
}

describe("Entrar", () => {
  it("leva aos dois provedores pelo servidor, com a volta do ?volta=", async () => {
    setup("/entrar?volta=%2Fbancada%2Fcsv-janitor");

    await expectHeadingFocused("Entrar no GoblinShift");
    const main = screen.getByRole("main");
    expect(await within(main).findByRole("link", { name: "Entrar com Google" })).toHaveAttribute(
      "href",
      "/auth/login/google?return_to=%2Fbancada%2Fcsv-janitor",
    );
    expect(within(main).getByRole("link", { name: "Entrar com GitHub" })).toHaveAttribute(
      "href",
      "/auth/login/github?return_to=%2Fbancada%2Fcsv-janitor",
    );
  });

  it("?volta= para outro site vira a Oficina", async () => {
    setup("/entrar?volta=https%3A%2F%2Fevil.example%2F");

    expect(await screen.findByRole("link", { name: "Entrar com Google" })).toHaveAttribute(
      "href",
      "/auth/login/google?return_to=%2F",
    );
  });

  it("linka os Termos de Uso e o Aviso de Privacidade", async () => {
    setup("/entrar");

    // O rodapé também linka os dois; aqui vale o aviso junto dos botões de login.
    const main = within(await screen.findByRole("main"));
    expect(await main.findByRole("link", { name: "Termos de Uso" })).toHaveAttribute(
      "href",
      "/termos",
    );
    expect(main.getByRole("link", { name: "Aviso de Privacidade" })).toHaveAttribute(
      "href",
      "/privacidade",
    );
  });

  it("nenhum pedido de login sai por fetch: o login é navegação", async () => {
    const { api } = setup("/entrar");
    await screen.findByRole("link", { name: "Entrar com Google" });

    expect(screenRequests(api.requests)).toEqual([]);
  });

  it("com sessão, diz com que conta entrou e não oferece outro login", async () => {
    setup("/entrar", () => jsonResponse(meBody()));

    expect(await screen.findByText("Você já entrou como Ana.")).toBeVisible();
    expect(screen.getByRole("link", { name: "Ver o seu acesso" })).toHaveAttribute(
      "href",
      "/acesso",
    );
    expect(screen.queryByRole("link", { name: "Entrar com Google" })).toBeNull();
  });

  it("não tem violações do axe", async () => {
    const { container } = setup("/entrar");
    await screen.findByRole("link", { name: "Entrar com GitHub" });

    expect(await axeViolations(container)).toEqual([]);
  });
});
