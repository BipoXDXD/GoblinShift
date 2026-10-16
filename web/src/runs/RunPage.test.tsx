import { screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

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
      ["queued", /^Rúbia, /],
      ["running", /^Rúbia, /],
      ["stopping", /^Rúbia, /],
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

describe("Execução do Media Mill: miniaturas como preview", () => {
  const THUMB_10 = "7d3c2a1e-0000-4000-8000-0000000000b1";
  const THUMB_90 = "7d3c2a1e-0000-4000-8000-0000000000b2";
  const AUDIO = "7d3c2a1e-0000-4000-8000-0000000000b3";
  const JPEG = new Uint8Array([0xff, 0xd8, 0xff, 0xe0, 0x00, 0x10, 0xff, 0xd9]);
  const mediaResults = {
    published: true,
    expires_at: "2026-10-21T12:00:00Z",
    artifacts: [
      {
        artifact_id: AUDIO,
        logical_name: "a",
        role: "output",
        file_name: "audio/férias.wav",
        availability: "available",
      },
      {
        artifact_id: THUMB_10,
        logical_name: "b",
        role: "output",
        file_name: "thumbnails/férias-10pct.jpg",
        availability: "available",
      },
      {
        artifact_id: THUMB_90,
        logical_name: "c",
        role: "output",
        file_name: "thumbnails/férias-90pct.jpg",
        availability: "available",
      },
    ],
  };
  const createObjectURL = vi.fn((blob: Blob) => `blob:fake-${String(blob.size)}`);
  const revokeObjectURL = vi.fn((url: string) => url);

  beforeEach(() => {
    vi.stubGlobal("URL", Object.assign(URL, { createObjectURL, revokeObjectURL }));
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    createObjectURL.mockClear();
    revokeObjectURL.mockClear();
  });

  const download = (id: string) => `GET /api/v1/artifacts/${id}/download`;
  const jpeg = () =>
    new Response(JPEG, { headers: { "Content-Type": "application/octet-stream" } });

  function setupMedia(thumb90: Handler = jpeg) {
    return setup(
      () => jsonResponse(runBody({ recipe_id: "media-mill", status: "succeeded" })),
      () => jsonResponse(mediaResults),
      `/execucoes/${RUN_ID}`,
      {
        // A disponibilidade dos resultados vem do primeiro artefato (ResultsAvailability).
        [`GET /api/v1/artifacts/${AUDIO}`]: () =>
          jsonResponse({
            id: AUDIO,
            role: "output",
            size: 4,
            sha256: "0".repeat(64),
            expires_at: mediaResults.expires_at,
            availability: "available",
          }),
        [download(THUMB_10)]: jpeg,
        [download(THUMB_90)]: thumb90,
      },
    );
  }

  it("mostra cada miniatura como imagem, buscando uma de cada vez", async () => {
    const { api } = setupMedia();

    const previews = await screen.findByRole("region", { name: "Miniaturas" });
    expect(
      await within(previews).findByRole("img", { name: "Miniatura férias-90pct.jpg" }),
    ).toHaveAttribute("src", "blob:fake-8");
    expect(within(previews).getByRole("img", { name: "Miniatura férias-10pct.jpg" })).toBeVisible();
    const downloads = screenRequests(api.requests)
      .map((request) => new URL(request.url).pathname)
      .filter((pathname) => pathname.endsWith("/download"));
    expect(downloads).toEqual([
      `/api/v1/artifacts/${THUMB_10}/download`,
      `/api/v1/artifacts/${THUMB_90}/download`,
    ]);
    expect(createObjectURL.mock.calls.map(([blob]) => blob.type)).toEqual([
      "image/jpeg",
      "image/jpeg",
    ]);
  });

  it("corpo que não é JPEG fica como falha só daquela miniatura", async () => {
    setupMedia(() => new Response("<svg onload=alert(1)>", { status: 200 }));

    const previews = await screen.findByRole("region", { name: "Miniaturas" });
    expect(
      await within(previews).findByText(/Não foi possível mostrar esta miniatura/),
    ).toBeVisible();
    expect(within(previews).getAllByRole("img")).toHaveLength(1);
  });

  it("revoga as URLs ao sair da tela", async () => {
    const { unmount } = setupMedia();
    await screen.findByRole("img", { name: "Miniatura férias-90pct.jpg" });

    unmount();

    expect(revokeObjectURL.mock.calls.map(([url]) => url)).toEqual(["blob:fake-8", "blob:fake-8"]);
  });

  it("outra receita não busca miniaturas", async () => {
    const { api } = setup(
      () => jsonResponse(runBody({ status: "succeeded" })),
      () => jsonResponse(mediaResults),
    );

    await screen.findByRole("link", { name: "Baixar thumbnails/férias-10pct.jpg" });
    expect(screen.queryByRole("region", { name: "Miniaturas" })).toBeNull();
    expect(screenRequests(api.requests).some((r) => r.url.includes("/download"))).toBe(false);
  });

  it("não tem violações do axe com as miniaturas", async () => {
    const { container } = setupMedia();
    await screen.findByRole("img", { name: "Miniatura férias-90pct.jpg" });

    expect(await axeViolations(container)).toEqual([]);
  });
});
