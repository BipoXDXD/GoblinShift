import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { createRoutes } from "../app/routes";
import { axeViolations } from "../test/axe";
import { fakeApi, jsonResponse, problemResponse, runBody, RUN_ID } from "../test/fakeApi";
import { fakeUploader } from "../test/fakeUploader";
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
  const { uploader } = fakeUploader({ autoResolve: true });
  const routes = createRoutes({
    uploadServices: {
      client: api.client,
      uploader,
      rateLimit: { maxAttempts: 1, fallbackDelayMs: 0, random: () => 0 },
    },
  });
  return { ...renderRoutes({ routes, path, client: api.client }), api };
}

describe("Execução", () => {
  it("abre com o foco no título e mostra o carregamento", async () => {
    setup(() => null);

    expect(await screen.findByRole("heading", { level: 1, name: "Execução" })).toHaveFocus();
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
    expect(api.requests).toHaveLength(0);
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

  it("não tem violações do axe", async () => {
    const { container } = setup(() => jsonResponse(runBody({ status: "succeeded" })));
    await screen.findByRole("link", { name: "Baixar clientes.csv" });

    expect(await axeViolations(container)).toEqual([]);
  });
});
