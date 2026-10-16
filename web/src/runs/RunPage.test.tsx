import { fireEvent, screen, within } from "@testing-library/react";
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
      source_artifact_id: null,
    },
  ],
};

type Handler = () => Response | null;

function setup(
  run: Handler,
  results: Handler = () => jsonResponse(publishedResults),
  path = `/execucoes/${RUN_ID}`,
  extra: Readonly<Record<string, Handler>> = {},
) {
  const api = fakeApi({
    [`GET /api/v1/runs/${RUN_ID}`]: run,
    [`GET /api/v1/runs/${RUN_ID}/results`]: results,
    ...extra,
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
              source_artifact_id: null,
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

describe("pré-visualização dos resultados", () => {
  const OUTPUT_ID = "7d3c2a1e-0000-4000-8000-0000000000b1";
  const PREVIEW_ID = "7d3c2a1e-0000-4000-8000-0000000000b2";
  const PREVIEW_ROUTE = `GET /api/v1/artifacts/${OUTPUT_ID}/preview`;

  interface Pair {
    readonly fileName: string;
    readonly previewName: string;
    readonly availability?: string;
  }

  function resultsWithPreview({ fileName, previewName, availability = "available" }: Pair) {
    return {
      ...publishedResults,
      artifacts: [
        {
          artifact_id: OUTPUT_ID,
          logical_name: fileName,
          role: "output",
          file_name: fileName,
          availability,
          source_artifact_id: null,
        },
        {
          artifact_id: PREVIEW_ID,
          logical_name: `previews/${previewName}`,
          role: "preview",
          file_name: previewName,
          availability,
          source_artifact_id: OUTPUT_ID,
        },
      ],
    };
  }

  function setupPreview(pair: Pair, preview: Handler = () => null) {
    return setup(
      () => jsonResponse(runBody({ status: "succeeded" })),
      () => jsonResponse(resultsWithPreview(pair)),
      `/execucoes/${RUN_ID}`,
      { [PREVIEW_ROUTE]: preview },
    );
  }

  function bytes(body: string): Response {
    return new Response(body, { headers: { "Content-Type": "application/octet-stream" } });
  }

  it("o preview não aparece como download", async () => {
    setupPreview({ fileName: "foto.png", previewName: "foto.jpg" });

    await screen.findByRole("link", { name: "Baixar foto.png" });
    expect(screen.getAllByRole("link", { name: /^Baixar / })).toHaveLength(1);
    expect(screen.queryByText(/pré-visualização\)/)).toBeNull();
  });

  it("imagem: <img> de tamanho fixo pela rota de preview da saída", async () => {
    setupPreview({ fileName: "foto.png", previewName: "foto.jpg" });

    const image = await screen.findByRole("img", { name: "Pré-visualização de foto.png" });
    expect(image).toHaveAttribute("src", `/api/v1/artifacts/${OUTPUT_ID}/preview`);
    expect(image).toHaveClass("size-40", "object-contain");
  });

  it("imagem: mostra o carregamento até o load", async () => {
    setupPreview({ fileName: "foto.png", previewName: "foto.jpg" });
    const image = await screen.findByRole("img", { name: "Pré-visualização de foto.png" });
    expect(screen.getByText("Carregando a pré-visualização.")).toBeInTheDocument();

    fireEvent.load(image);

    expect(screen.queryByText("Carregando a pré-visualização.")).toBeNull();
    expect(image).toBeVisible();
  });

  it("imagem: falha ao carregar troca a imagem pelo aviso", async () => {
    setupPreview({ fileName: "foto.png", previewName: "foto.jpg" });
    const image = await screen.findByRole("img", { name: "Pré-visualização de foto.png" });

    fireEvent.error(image);

    expect(screen.getByText("Não foi possível carregar a pré-visualização.")).toBeVisible();
    expect(screen.queryByRole("img", { name: "Pré-visualização de foto.png" })).toBeNull();
  });

  it("CSV: amostra em tabela com o cabeçalho como colunas", async () => {
    setupPreview({ fileName: "clientes.csv", previewName: "clientes.csv" }, () =>
      bytes("nome,email\nAna,ana@example.com\n"),
    );

    const table = await screen.findByRole("table", { name: "Amostra de clientes.csv: 1 registro" });
    expect(
      within(table)
        .getAllByRole("columnheader")
        .map((th) => th.textContent),
    ).toEqual(["nome", "email"]);
    expect(within(table).getByRole("cell", { name: "ana@example.com" })).toBeInTheDocument();
  });

  it("CSV: fórmula e HTML aparecem como texto, nunca como elemento", async () => {
    setupPreview({ fileName: "clientes.csv", previewName: "clientes.csv" }, () =>
      bytes('nome,obs\n"\t=cmd|\' /C calc\'!A0","<img src=x onerror=alert(1)>"\n'),
    );

    const table = await screen.findByRole("table");
    const [formula, html] = within(table).getAllByRole("cell");
    expect(formula?.textContent).toBe("\t=cmd|' /C calc'!A0");
    expect(html?.textContent).toBe("<img src=x onerror=alert(1)>");
    expect(table.querySelector("img")).toBeNull();
  });

  it("CSV: carregando enquanto a amostra não chega", async () => {
    setupPreview({ fileName: "clientes.csv", previewName: "clientes.csv" });

    expect(await screen.findByText("Carregando a pré-visualização.")).toBeInTheDocument();
  });

  it("CSV: amostra expirada (410) diz que ela não existe mais", async () => {
    setupPreview({ fileName: "clientes.csv", previewName: "clientes.csv" }, () =>
      problemResponse(410, "GONE"),
    );

    expect(await screen.findByText("A pré-visualização expirou ou foi excluída.")).toBeVisible();
  });

  it("CSV malformado mostra a falha, sem tabela", async () => {
    setupPreview({ fileName: "clientes.csv", previewName: "clientes.csv" }, () =>
      bytes('nome\n"sem fim\n'),
    );

    expect(await screen.findByText("Não foi possível carregar a pré-visualização.")).toBeVisible();
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("PDF: mapa de páginas em ordem", async () => {
    setupPreview({ fileName: "selecao.pdf", previewName: "selecao.json" }, () =>
      bytes(
        JSON.stringify({
          schema_version: 1,
          path: "selecao.pdf",
          pages: [
            { input_position: 1, page_number: 3 },
            { input_position: 0, page_number: 1 },
          ],
        }),
      ),
    );

    const list = await screen.findByRole("list", { name: "Páginas de selecao.pdf: 2 páginas" });
    expect(
      within(list)
        .getAllByRole("listitem")
        .map((li) => li.textContent),
    ).toEqual(["Página 3 do arquivo 2", "Página 1 do arquivo 1"]);
  });

  it("PDF: mapa fora do formato mostra a falha", async () => {
    setupPreview({ fileName: "selecao.pdf", previewName: "selecao.json" }, () =>
      bytes(JSON.stringify({ pages: "todas" })),
    );

    expect(await screen.findByText("Não foi possível carregar a pré-visualização.")).toBeVisible();
  });

  it.each([
    ["expired", "Pré-visualização indisponível: o arquivo expirou."],
    ["deleted", "Pré-visualização indisponível: o arquivo foi excluído."],
  ])("indisponível (%s) diz o motivo sem pedir o preview", async (availability, message) => {
    const { api } = setupPreview({
      fileName: "clientes.csv",
      previewName: "clientes.csv",
      availability,
    });

    expect(await screen.findByText(message)).toBeVisible();
    expect(api.requests.some((request) => new URL(request.url).pathname.endsWith("/preview"))).toBe(
      false,
    );
  });

  it("preview de formato desconhecido não aparece", async () => {
    const { api } = setupPreview({ fileName: "notas.txt", previewName: "notas.webp" });

    await screen.findByRole("link", { name: "Baixar notas.txt" });
    expect(screen.queryByText(/pré-visualização/i)).toBeNull();
    expect(api.requests.some((request) => new URL(request.url).pathname.endsWith("/preview"))).toBe(
      false,
    );
  });

  it("não tem violações do axe com a amostra CSV", async () => {
    const { container } = setupPreview(
      { fileName: "clientes.csv", previewName: "clientes.csv" },
      () => bytes("nome,email\nAna,ana@example.com\n"),
    );
    await screen.findByRole("table");

    expect(await axeViolations(container)).toEqual([]);
  });
});
