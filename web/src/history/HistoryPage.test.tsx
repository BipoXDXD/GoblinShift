import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { appRoutes } from "../test/appRoutes";
import { axeViolations } from "../test/axe";
import { fakeApi, jsonResponse, problemResponse, runBody } from "../test/fakeApi";
import { expectHeadingFocused } from "../test/focus";
import { renderRoutes } from "../test/renderRoutes";

const RUN_A = "0b9c3c1e-7f62-4d1c-9a49-0d6f5d0b8f3a";
const RUN_B = "1c8d4e2f-0000-4000-8000-0000000000bb";

type Handler = (request: Request) => Response | null;

function page(runs: readonly Record<string, unknown>[], nextCursor: string | null = null) {
  return jsonResponse({ data: runs, next_cursor: nextCursor });
}

function setup(runs: Handler, path = "/historico") {
  const api = fakeApi({ "GET /api/v1/runs": runs });
  const view = renderRoutes({ routes: appRoutes(api.client), path, client: api.client });
  return { ...view, api, user: userEvent.setup() };
}

/** Caminho e query de cada GET /runs, na ordem. */
function listCalls(requests: readonly Request[]): string[] {
  return requests
    .map((request) => new URL(request.url))
    .filter((url) => url.pathname === "/api/v1/runs")
    .map((url) => `${url.pathname}${url.search}`);
}

describe("Histórico", () => {
  it("abre com o foco no título e lista as execuções com estado e link", async () => {
    const { api } = setup(() =>
      page([
        runBody({ id: RUN_A, status: "succeeded" }),
        runBody({ id: RUN_B, recipe_id: "image-forge", status: "failed" }),
      ]),
    );

    await expectHeadingFocused("Histórico");
    const list = await screen.findByRole("list", { name: "Execuções" });
    const rows = within(list).getAllByRole("listitem");
    expect(rows).toHaveLength(2);
    expect(
      within(rows[0] ?? list).getByRole("link", { name: /^CSV Janitor, criada em / }),
    ).toHaveAttribute("href", `/execucoes/${RUN_A}`);
    expect(rows[0]).toHaveTextContent("Concluída");
    expect(
      within(rows[1] ?? list).getByRole("link", { name: /^Image Forge, criada em / }),
    ).toHaveAttribute("href", `/execucoes/${RUN_B}`);
    expect(rows[1]).toHaveTextContent("Falhou");
    expect(listCalls(api.requests)).toEqual(["/api/v1/runs?limit=25"]);
  });

  it("sem execuções e sem filtros, mostra o estado vazio sem os filtros", async () => {
    setup(() => page([]));

    expect(await screen.findByRole("heading", { name: "Nenhuma execução ainda" })).toBeVisible();
    // Rotação estável: o Histórico vazio cai sempre na Jade.
    expect(screen.getByRole("img", { name: /^Jade, / })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Escolher uma receita na Oficina" })).toHaveAttribute(
      "href",
      "/",
    );
    expect(screen.queryByText("Filtros")).toBeNull();
  });

  it("com filtros e nenhum resultado, oferece limpar os filtros", async () => {
    setup(() => page([]), "/historico?estado=failed");

    expect(
      await screen.findByRole("heading", { name: "Nenhuma execução com esses filtros" }),
    ).toBeVisible();
    expect(screen.getByRole("link", { name: "Limpar filtros" })).toHaveAttribute(
      "href",
      "/historico",
    );
  });

  it("filtros válidos da URL vão para a API e aparecem no formulário aberto", async () => {
    const { api } = setup(
      () => page([runBody({ id: RUN_A, recipe_id: "image-forge", status: "failed" })]),
      "/historico?receita=image-forge&estado=failed&periodo=7d",
    );

    await screen.findByRole("list", { name: "Execuções" });
    expect(listCalls(api.requests)).toEqual([
      "/api/v1/runs?limit=25&recipe_id=image-forge&status=failed&created_from=2026-10-07T12%3A00%3A00.000Z",
    ]);
    expect(screen.getByText("Filtros").closest("details")).toHaveAttribute("open");
    expect(screen.getByLabelText("Receita")).toHaveValue("image-forge");
    expect(screen.getByLabelText("Estado")).toHaveValue("failed");
    expect(screen.getByLabelText("Período")).toHaveValue("7d");
  });

  it("parâmetro forjado na URL não chega à API", async () => {
    const { api } = setup(
      () => page([runBody()]),
      "/historico?estado=hacked&receita=..%2Fadmin&periodo=1y&limit=1000&cursor=x",
    );

    await screen.findByRole("list", { name: "Execuções" });
    expect(listCalls(api.requests)).toEqual(["/api/v1/runs?limit=25"]);
  });

  it("aplicar os filtros grava a URL e pede a lista filtrada", async () => {
    const { api, user } = setup(() => page([runBody()]));
    await screen.findByRole("list", { name: "Execuções" });

    await user.click(screen.getByText("Filtros"));
    await user.selectOptions(screen.getByLabelText("Estado"), "Falhou");
    await user.selectOptions(screen.getByLabelText("Receita"), "Downloads Detox");
    await user.click(screen.getByRole("button", { name: "Aplicar filtros" }));

    await waitFor(() => {
      expect(listCalls(api.requests)).toEqual([
        "/api/v1/runs?limit=25",
        "/api/v1/runs?limit=25&recipe_id=downloads-detox&status=failed",
      ]);
    });
    // A lista filtrada vem da URL (fonte da verdade); o formulário recriado mostra os filtros.
    expect(await screen.findByRole("link", { name: "Limpar filtros" })).toHaveAttribute(
      "href",
      "/historico",
    );
    expect(screen.getByLabelText("Estado")).toHaveValue("failed");
  });

  describe("Carregar mais (cursor)", () => {
    it("segue o cursor com os mesmos filtros e acrescenta as linhas", async () => {
      const { api, user } = setup(
        (request) =>
          new URL(request.url).searchParams.get("cursor") === "pagina-2"
            ? page([runBody({ id: RUN_B, status: "canceled" })])
            : page([runBody({ id: RUN_A })], "pagina-2"),
        "/historico?estado=queued",
      );

      await user.click(await screen.findByRole("button", { name: "Carregar mais" }));

      await waitFor(() => {
        expect(screen.getAllByRole("listitem")).toHaveLength(2);
      });
      expect(listCalls(api.requests)).toEqual([
        "/api/v1/runs?limit=25&status=queued",
        "/api/v1/runs?limit=25&status=queued&cursor=pagina-2",
      ]);
      expect(screen.getByRole("status")).toHaveTextContent("Mais 1 execução na lista.");
      expect(screen.queryByRole("button", { name: "Carregar mais" })).toBeNull();
    });

    it("cursor vencido (400) recomeça a lista do início e avisa", async () => {
      const { api, user } = setup((request) =>
        new URL(request.url).searchParams.has("cursor")
          ? problemResponse(400, "BAD_REQUEST")
          : page([runBody({ id: RUN_A })], "vencido"),
      );

      await user.click(await screen.findByRole("button", { name: "Carregar mais" }));

      await waitFor(() => {
        expect(screen.getByRole("status")).toHaveTextContent(
          "A lista voltou ao início, porque a página seguinte expirou.",
        );
      });
      expect(listCalls(api.requests)).toEqual([
        "/api/v1/runs?limit=25",
        "/api/v1/runs?limit=25&cursor=vencido",
        "/api/v1/runs?limit=25",
      ]);
      expect(screen.queryByRole("alert")).toBeNull();
      expect(screen.getAllByRole("listitem")).toHaveLength(1);
    });

    it("outra falha na página seguinte mantém a lista e mostra o erro", async () => {
      const { user } = setup((request) =>
        new URL(request.url).searchParams.has("cursor")
          ? problemResponse(503, "SERVICE_UNAVAILABLE")
          : page([runBody({ id: RUN_A })], "pagina-2"),
      );

      await user.click(await screen.findByRole("button", { name: "Carregar mais" }));

      expect(await screen.findByRole("alert")).toHaveTextContent(/indisponível/);
      expect(screen.getAllByRole("listitem")).toHaveLength(1);
      expect(screen.getByRole("button", { name: "Carregar mais" })).toBeVisible();
    });
  });

  it("falha da primeira página aparece com texto próprio", async () => {
    setup(() => problemResponse(503, "SERVICE_UNAVAILABLE"));

    expect(await screen.findByRole("alert")).toHaveTextContent(/indisponível/);
  });

  it("sem login, convida a entrar e volta para o Histórico com os filtros", async () => {
    setup(() => problemResponse(401, "AUTHENTICATION_REQUIRED"), "/historico?estado=failed");

    const prompt = await screen.findByRole("region", { name: "Entre para continuar" });
    expect(within(prompt).getByRole("link", { name: "Entrar com Google" })).toHaveAttribute(
      "href",
      "/auth/login/google?return_to=%2Fhistorico%3Festado%3Dfailed",
    );
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("não tem violações do axe com lista e filtros", async () => {
    const { container } = setup(() => page([runBody()], "pagina-2"), "/historico?estado=queued");
    await screen.findByRole("button", { name: "Carregar mais" });

    expect(await axeViolations(container)).toEqual([]);
  });
});
