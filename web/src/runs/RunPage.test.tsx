import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { appRoutes } from "../test/appRoutes";
import { axeViolations } from "../test/axe";
import {
  fakeApi,
  jsonResponse,
  problemResponse,
  runBody,
  RUN_ID,
  screenRequests,
} from "../test/fakeApi";
import { expectHeadingFocused } from "../test/focus";
import { renderRoutes } from "../test/renderRoutes";

const ARTIFACT_ID = "7d3c2a1e-0000-4000-8000-0000000000aa";

const publishedResults = {
  published: true,
  expires_at: "2026-10-21T12:00:00Z",
  artifacts: [
    {
      artifact_id: ARTIFACT_ID,
      logical_name: "clientes",
      role: "output",
      file_name: "clientes.csv",
      availability: "available",
    },
  ],
};

type Handler = () => Response | null;

function setup(
  run: Handler,
  results: Handler = () => jsonResponse(publishedResults),
  path = `/execucoes/${RUN_ID}`,
) {
  const api = fakeApi({
    [`GET /api/v1/runs/${RUN_ID}`]: run,
    [`GET /api/v1/runs/${RUN_ID}/results`]: results,
  });
  return { ...renderRoutes({ routes: appRoutes(api.client), path, client: api.client }), api };
}

describe("Execução", () => {
  it("abre com o foco no título e mostra o carregamento", async () => {
    setup(() => null);

    await expectHeadingFocused("Execução");
    expect(screen.getByText("Carregando a execução.")).toBeInTheDocument();
  });

  it("id que não é UUID mostra a página não encontrada, sem chamar a API", async () => {
    const { api } = setup(
      () => null,
      () => null,
      "/execucoes/nao-e-uuid",
    );

    expect(
      await screen.findByRole("heading", { level: 1, name: "Página não encontrada" }),
    ).toBeVisible();
    expect(screenRequests(api.requests)).toHaveLength(0);
    expect(screen.getByRole("img", { name: /^Nyx, / })).toBeInTheDocument();
  });

  it("mostra estado, fase e progresso em texto", async () => {
    setup(() =>
      jsonResponse(
        runBody({ status: "running", progress: { phase: "processing", processed: 2, total: 5 } }),
      ),
    );

    expect(await screen.findByText("Em andamento")).toBeInTheDocument();
    expect(screen.getByText("Processando os arquivos")).toBeInTheDocument();
    expect(screen.getByText("2 de 5")).toBeInTheDocument();
  });

  it("na fila, mostra quantas execuções estão à frente", async () => {
    setup(() => jsonResponse(runBody({ status: "queued", queue_ahead: 3 })));

    expect(await screen.findByText("Execuções à frente na fila")).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument();
  });

  it("concluída, lista os downloads como links de navegação", async () => {
    setup(() => jsonResponse(runBody({ status: "succeeded" })));

    const link = await screen.findByRole("link", { name: "Baixar clientes.csv" });
    expect(link).toHaveAttribute("href", `/api/v1/artifacts/${ARTIFACT_ID}/download`);
    expect(link).toHaveAttribute("download", "clientes.csv");
    const results = screen.getByRole("region", { name: "Resultados" });
    expect(within(results).getByText(/arquivo processado/)).toBeInTheDocument();
  });

  it("o link do pacote diz o papel do arquivo separado do nome", async () => {
    setup(
      () => jsonResponse(runBody({ status: "succeeded" })),
      () =>
        jsonResponse({
          ...publishedResults,
          artifacts: [
            {
              artifact_id: ARTIFACT_ID,
              logical_name: "pacote",
              role: "package",
              file_name: "resultado.zip",
              availability: "available",
            },
          ],
        }),
    );

    expect(
      await screen.findByRole("link", {
        name: "Baixar resultado.zip (pacote com todos os arquivos)",
      }),
    ).toHaveAttribute("download", "resultado.zip");
  });

  it("concluída sem arquivos publicados mostra o estado vazio", async () => {
    setup(
      () => jsonResponse(runBody({ status: "succeeded" })),
      () => jsonResponse({ published: false, expires_at: null, artifacts: [] }),
    );

    expect(
      await screen.findByRole("heading", { name: "Nenhum arquivo para baixar" }),
    ).toBeVisible();
  });

  it("falha da execução mostra o motivo em texto", async () => {
    setup(() =>
      jsonResponse(
        runBody({
          status: "failed",
          error: { code: "UNSUPPORTED_FORMAT", message: "clientes.csv não é UTF-8" },
        }),
      ),
    );

    expect(await screen.findByText("Falhou")).toBeInTheDocument();
    expect(screen.getByText("clientes.csv não é UTF-8")).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Resultados" })).toBeNull();
  });

  it("execução alheia ou inexistente mostra o 404 com texto próprio", async () => {
    setup(() => problemResponse(404, "NOT_FOUND"));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Não encontramos o que você pediu.");
    expect(alert).not.toHaveTextContent("Detail from server");
  });

  it("falha ao ler os resultados aparece como alerta", async () => {
    setup(
      () => jsonResponse(runBody({ status: "succeeded" })),
      () => problemResponse(410, "GONE"),
    );

    expect(await screen.findByRole("alert")).toHaveTextContent("Isto expirou");
  });

  it("oferece uma nova execução na mesma receita", async () => {
    setup(() => jsonResponse(runBody({ status: "succeeded" })));

    expect(
      await screen.findByRole("link", { name: "Nova execução nesta receita" }),
    ).toHaveAttribute("href", "/bancada/csv-janitor");
  });

  describe("goblin do estado", () => {
    it.each([
      ["queued", /^Ada, /],
      ["running", /^Ada, /],
      ["stopping", /^Ada, /],
      ["succeeded", /^Ferrugem, /],
      ["succeeded_with_warnings", /^Ferrugem, /],
      ["failed", /^Nyx, /],
    ])("%s mostra a goblin do estado", async (status, goblin) => {
      setup(() => jsonResponse(runBody({ status })));

      expect(await screen.findByRole("img", { name: goblin })).toBeInTheDocument();
      expect(screen.getAllByRole("img")).toHaveLength(1);
    });

    it("cancelada não tem goblin: não é espera, sucesso nem erro", async () => {
      setup(() => jsonResponse(runBody({ status: "canceled" })));

      expect(await screen.findByText("Cancelada")).toBeInTheDocument();
      expect(screen.queryByRole("img")).toBeNull();
    });
  });

  it("não tem violações do axe", async () => {
    const { container } = setup(() => jsonResponse(runBody({ status: "succeeded" })));
    await screen.findByRole("link", { name: "Baixar clientes.csv" });

    expect(await axeViolations(container)).toEqual([]);
  });
});
