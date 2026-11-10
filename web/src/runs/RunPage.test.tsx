import { notifyManager, type QueryClient } from "@tanstack/react-query";
import { act, fireEvent, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { appRoutes } from "../test/appRoutes";
import { axeViolations } from "../test/axe";
import {
  fakeApi,
  type FakeApi,
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
    expect(link).toHaveClass("min-h-11");
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

/** Respostas do GET da execução em sequência; a última se repete. */
function inSequence(...responses: readonly (() => Response | null)[]): Handler {
  let served = 0;
  return () => {
    const next = responses[Math.min(served, responses.length - 1)];
    served += 1;
    return next === undefined ? null : next();
  };
}

const running = (): Response => jsonResponse(runBody({ status: "running" }));
const unavailable = (): Response => problemResponse(503, "SERVICE_UNAVAILABLE");
const UNAVAILABLE_TEXT = "O serviço está indisponível no momento.";
const SIGNED_OUT_TEXT = "Sua sessão terminou. Entre de novo para continuar.";
/** Quantas vezes a tela consultou a execução. */
function runRequests(requests: readonly Request[]): number {
  return requests.filter(
    (request) =>
      request.method === "GET" && new URL(request.url).pathname === `/api/v1/runs/${RUN_ID}`,
  ).length;
}

const FORBIDDEN_TEXT = "Você não tem permissão para esta ação.";
const NOT_FOUND_TEXT = "Não encontramos o que você pediu.";
const TIMEOUT_TEXT = "O servidor demorou demais para responder.";
/** Prazo do cliente da API (client.ts), que o relógio falso atravessa no teste do timeout. */
const CLIENT_TIMEOUT_MS = 30_000;

type PollFailure = (queryClient: QueryClient, api: FakeApi) => Promise<void>;

/** As próximas `polls` consultas da execução respondem com a falha. */
function pollFailing(failure: () => Response, polls = 1): PollFailure {
  return async (queryClient, api) => {
    for (let poll = 0; poll < polls; poll += 1) {
      api.fetchMock.mockImplementationOnce(() => Promise.resolve(failure()));
      await nextPoll(queryClient);
    }
  };
}

/** A próxima consulta não responde até o prazo do cliente, que a aborta como timeout. */
const pollTimingOut: PollFailure = async (queryClient, api) => {
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
  try {
    api.fetchMock.mockImplementationOnce(
      (_input, init) =>
        new Promise<Response>((_resolve, reject) => {
          init?.signal?.addEventListener("abort", () => {
            reject(new DOMException("Prazo do cliente", "AbortError"));
          });
        }),
    );
    void queryClient.refetchQueries({ queryKey: ["runs", RUN_ID] });
    await act(() => vi.advanceTimersByTimeAsync(CLIENT_TIMEOUT_MS));
  } finally {
    vi.useRealTimers();
  }
};

// Pedido que nunca responde: segura a consulta em andamento.
const hanging = (): null => null;

/** A próxima consulta do polling, sem esperar o intervalo dele. */
async function nextPoll(queryClient: QueryClient): Promise<void> {
  await act(() => queryClient.refetchQueries({ queryKey: ["runs", RUN_ID] }));
}

/**
 * Conta os `role="alert"` inseridos no DOM: cada inserção é um anúncio assertivo do leitor de
 * tela. Um alerta que fica montado e só muda de texto não conta.
 */
function countInsertedAlerts(): { readonly count: () => number; readonly stop: () => void } {
  let inserted = 0;
  const tally = (mutations: readonly MutationRecord[]): void => {
    for (const mutation of mutations) {
      for (const node of mutation.addedNodes) {
        if (node instanceof Element) {
          inserted += node.matches('[role="alert"]') ? 1 : 0;
          inserted += node.querySelectorAll('[role="alert"]').length;
        }
      }
    }
  };
  const observer = new MutationObserver(tally);
  observer.observe(document.body, { childList: true, subtree: true });
  return {
    count: () => {
      tally(observer.takeRecords());
      return inserted;
    },
    stop: () => {
      observer.disconnect();
    },
  };
}

describe("Execução: falha do polling sem anúncio repetido", () => {
  beforeEach(() => {
    // O TanStack Query entrega as atualizações num setTimeout(0); entregar na hora faz cada
    // consulta virar uma renderização, como na tela, onde as consultas têm segundos entre si.
    notifyManager.setScheduler((callback) => {
      callback();
    });
  });

  afterEach(() => {
    notifyManager.setScheduler((callback) => setTimeout(callback, 0));
  });

  it.each([
    ["403 comum", pollFailing(() => problemResponse(403, "FORBIDDEN")), FORBIDDEN_TEXT],
    ["404", pollFailing(() => problemResponse(404, "NOT_FOUND")), NOT_FOUND_TEXT],
    ["5xx persistente", pollFailing(unavailable, 2), UNAVAILABLE_TEXT],
    ["timeout", pollTimingOut, TIMEOUT_TEXT],
  ])("falha que vira alerta (%s) mantém o Cancelar na tela", async (_name, fail, text) => {
    const { queryClient, api } = setup(running);
    expect(await screen.findByRole("button", { name: "Cancelar execução" })).toBeVisible();

    await fail(queryClient, api);

    expect(screen.getByRole("alert")).toHaveTextContent(text);
    expect(screen.getByRole("button", { name: "Cancelar execução" })).toBeVisible();
  });

  it("falha intermitente (erro, sucesso, erro) não vira alerta a cada alternância", async () => {
    const { queryClient } = setup(inSequence(running, unavailable, running, unavailable));
    expect(await screen.findByText("Em andamento")).toBeInTheDocument();
    const alerts = countInsertedAlerts();

    await nextPoll(queryClient);
    await nextPoll(queryClient);
    await nextPoll(queryClient);

    expect(alerts.count()).toBe(0);
    expect(screen.queryByRole("alert")).toBeNull();
    alerts.stop();
  });

  it("falha persistente vira um alerta só, que fica enquanto ela dura", async () => {
    const { queryClient } = setup(inSequence(running, unavailable));
    expect(await screen.findByText("Em andamento")).toBeInTheDocument();
    const alerts = countInsertedAlerts();

    await nextPoll(queryClient);
    await nextPoll(queryClient);
    expect(screen.getByRole("alert")).toHaveTextContent(UNAVAILABLE_TEXT);
    await nextPoll(queryClient);
    await nextPoll(queryClient);

    expect(alerts.count()).toBe(1);
    expect(screen.getByRole("alert")).toHaveTextContent(UNAVAILABLE_TEXT);
    // O último estado conhecido continua na tela, abaixo do aviso de que pode estar desatualizado.
    expect(screen.getByText("Em andamento")).toBeInTheDocument();
    alerts.stop();
  });

  it("sessão terminada é anunciada uma vez só, na região de status, sem tirar o foco", async () => {
    const { queryClient } = setup(
      inSequence(running, () => problemResponse(401, "AUTHENTICATION_REQUIRED")),
    );
    expect(await screen.findByText("Em andamento")).toBeInTheDocument();
    await expectHeadingFocused("Execução");
    const alerts = countInsertedAlerts();

    await nextPoll(queryClient);
    await nextPoll(queryClient);

    expect(alerts.count()).toBe(0);
    const status = screen
      .getAllByRole("status")
      .filter((region) => region.textContent === SIGNED_OUT_TEXT);
    expect(status).toHaveLength(1);
    expect(screen.getAllByText(SIGNED_OUT_TEXT)).toHaveLength(1);
    expect(screen.getByRole("heading", { level: 1, name: "Execução" })).toHaveFocus();
    alerts.stop();
  });

  it("o alerta some quando o polling volta a responder", async () => {
    const { queryClient } = setup(inSequence(running, unavailable, unavailable, running));
    expect(await screen.findByText("Em andamento")).toBeInTheDocument();

    await nextPoll(queryClient);
    await nextPoll(queryClient);
    expect(screen.getByRole("alert")).toHaveTextContent(UNAVAILABLE_TEXT);
    await nextPoll(queryClient);

    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("ao voltar à execução, falhas antigas e não seguidas não viram alerta", async () => {
    const { queryClient, router } = setup(
      inSequence(running, unavailable, running, unavailable, hanging),
    );
    expect(await screen.findByText("Em andamento")).toBeInTheDocument();
    await nextPoll(queryClient);
    await nextPoll(queryClient);
    await nextPoll(queryClient);
    expect(screen.queryByRole("alert")).toBeNull();

    await act(() => router.navigate("/termos"));
    await act(() => router.navigate(`/execucoes/${RUN_ID}`));

    expect(await screen.findByText("Em andamento")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it.each([
    [403, "FORBIDDEN", "Você não tem permissão para esta ação."],
    [404, "NOT_FOUND", "Não encontramos o que você pediu"],
    [410, "GONE", "Isto expirou"],
  ])(
    "falha %i, que o polling não resolve sozinho, vira alerta na primeira vez e para o polling",
    async (status, code, text) => {
      // Só o intervalo do polling fica no relógio falso: avançá-lo prova que não há próxima consulta.
      vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
      try {
        const { queryClient, api } = setup(
          inSequence(running, () => problemResponse(status, code)),
        );
        expect(await screen.findByText("Em andamento")).toBeInTheDocument();

        await nextPoll(queryClient);
        expect(screen.getByRole("alert")).toHaveTextContent(text);
        await act(() => vi.advanceTimersByTimeAsync(60_000));

        expect(runRequests(api.requests)).toBe(2);
      } finally {
        vi.useRealTimers();
      }
    },
  );

  it.each([
    ["AUTHENTICATION_REQUIRED", "Entre para continuar", ""],
    ["REAUTH_REQUIRED", "Entre de novo para confirmar", "&reauth=1"],
  ])(
    "401 %s no polling mostra o convite para entrar de novo e para o polling",
    async (code, title, reauthParam) => {
      vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
      try {
        const { queryClient, api } = setup(inSequence(running, () => problemResponse(401, code)));
        expect(await screen.findByText("Em andamento")).toBeInTheDocument();

        await nextPoll(queryClient);
        await act(() => vi.advanceTimersByTimeAsync(60_000));

        const prompt = screen.getByRole("region", { name: title });
        expect(within(prompt).getByRole("heading", { level: 2, name: title })).toBeVisible();
        expect(within(prompt).getByRole("link", { name: "Entrar com Google" })).toHaveAttribute(
          "href",
          `/auth/login/google?return_to=%2Fexecucoes%2F${RUN_ID}${reauthParam}`,
        );
        expect(screen.queryByRole("alert")).toBeNull();
        expect(runRequests(api.requests)).toBe(2);
      } finally {
        vi.useRealTimers();
      }
    },
  );

  it("com o convite na tela, o Cancelar some e os dados já carregados ficam", async () => {
    const { queryClient } = setup(
      inSequence(running, () => problemResponse(401, "AUTHENTICATION_REQUIRED")),
    );
    expect(await screen.findByRole("button", { name: "Cancelar execução" })).toBeVisible();

    await nextPoll(queryClient);

    expect(screen.getByRole("region", { name: "Entre para continuar" })).toBeVisible();
    expect(screen.queryByRole("button", { name: "Cancelar execução" })).toBeNull();
    expect(screen.getByText("Em andamento")).toBeInTheDocument();
  });

  it("antes do primeiro dado, sessão terminada mostra o convite para entrar, sem alerta", async () => {
    setup(() => problemResponse(401, "AUTHENTICATION_REQUIRED"));

    expect(await screen.findByRole("region", { name: "Entre para continuar" })).toBeVisible();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("termos novos no polling levam ao aceite, como nas outras telas", async () => {
    const { queryClient } = setup(
      inSequence(running, () => problemResponse(403, "TERMS_NOT_ACCEPTED")),
    );
    expect(await screen.findByText("Em andamento")).toBeInTheDocument();

    await nextPoll(queryClient);

    expect(screen.getByRole("region", { name: "Aceite os termos para continuar" })).toBeVisible();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(
      screen.getAllByRole("status").some(
        // Sem distinguir maiúsculas: "termos de uso" vira "Termos de Uso" no #271.
        (region) =>
          /^Para continuar, aceite a versão atual dos termos de uso\.$/i.test(region.textContent),
      ),
    ).toBe(true);
  });

  it("falha transitória antes do primeiro dado vira alerta na primeira vez", async () => {
    setup(unavailable);

    expect(await screen.findByRole("alert")).toHaveTextContent(UNAVAILABLE_TEXT);
  });
});

describe("Execução do Media Mill: miniaturas pelo preview genérico", () => {
  const THUMB_10 = "7d3c2a1e-0000-4000-8000-0000000000b1";
  const THUMB_90 = "7d3c2a1e-0000-4000-8000-0000000000b2";
  const AUDIO = "7d3c2a1e-0000-4000-8000-0000000000b3";
  const PREVIEW_10 = "7d3c2a1e-0000-4000-8000-0000000000b4";
  const PREVIEW_90 = "7d3c2a1e-0000-4000-8000-0000000000b5";
  const output = (artifact_id: string, file_name: string) => ({
    artifact_id,
    logical_name: file_name,
    role: "output",
    file_name,
    availability: "available",
    source_artifact_id: null,
  });
  const preview = (artifact_id: string, file_name: string, source_artifact_id: string) => ({
    artifact_id,
    logical_name: `previews/${file_name}`,
    role: "preview",
    file_name,
    availability: "available",
    source_artifact_id,
  });
  const outputs = [
    output(AUDIO, "audio/férias.wav"),
    output(THUMB_10, "thumbnails/férias-10pct.jpg"),
    output(THUMB_90, "thumbnails/férias-90pct.jpg"),
  ];
  const withPreviews = {
    published: true,
    expires_at: "2026-10-21T12:00:00Z",
    artifacts: [
      ...outputs,
      preview(PREVIEW_10, "férias-10pct.jpg", THUMB_10),
      preview(PREVIEW_90, "férias-90pct.jpg", THUMB_90),
    ],
  };
  const PREVIEW_ALT = /^Pré-visualização de /;
  const createObjectURL = vi.fn((blob: Blob) => `blob:fake-${String(blob.size)}`);

  beforeEach(() => {
    vi.stubGlobal("URL", Object.assign(URL, { createObjectURL }));
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    createObjectURL.mockClear();
  });

  function setupMedia(results: unknown) {
    return setup(
      () => jsonResponse(runBody({ recipe_id: "media-mill", status: "succeeded" })),
      () => jsonResponse(results),
    );
  }

  function downloadsFetched(requests: readonly Request[]): boolean {
    return screenRequests(requests).some((request) =>
      new URL(request.url).pathname.endsWith("/download"),
    );
  }

  it("cada miniatura vira <img> na rota de preview dela, sem fetch nem Blob", async () => {
    const { api } = setupMedia(withPreviews);

    const image10 = await screen.findByRole("img", {
      name: "Pré-visualização de thumbnails/férias-10pct.jpg",
    });
    expect(image10).toHaveAttribute("src", `/api/v1/artifacts/${THUMB_10}/preview`);
    expect(
      screen.getByRole("img", { name: "Pré-visualização de thumbnails/férias-90pct.jpg" }),
    ).toHaveAttribute("src", `/api/v1/artifacts/${THUMB_90}/preview`);
    expect(screen.getAllByRole("img", { name: PREVIEW_ALT })).toHaveLength(2);
    expect(downloadsFetched(api.requests)).toBe(false);
    expect(createObjectURL).not.toHaveBeenCalled();
  });

  it("miniatura indisponível no /results não vira preview nem é buscada", async () => {
    const { api } = setupMedia({
      ...withPreviews,
      artifacts: withPreviews.artifacts.map((artifact) =>
        artifact.artifact_id === THUMB_90 || artifact.artifact_id === PREVIEW_90
          ? { ...artifact, availability: "missing" }
          : artifact,
      ),
    });

    expect(
      await screen.findByRole("img", { name: "Pré-visualização de thumbnails/férias-10pct.jpg" }),
    ).toBeVisible();
    expect(screen.getAllByRole("img", { name: PREVIEW_ALT })).toHaveLength(1);
    expect(screenRequests(api.requests).some((request) => request.url.includes(THUMB_90))).toBe(
      false,
    );
    // A lista já diz o motivo ao lado do nome; o preview não o repete.
    expect(screen.queryByText(/^Pré-visualização indisponível/)).toBeNull();
  });

  it("os previews não aparecem como download", async () => {
    setupMedia(withPreviews);

    await screen.findByRole("link", { name: "Baixar thumbnails/férias-10pct.jpg" });
    expect(screen.getAllByRole("link", { name: /^Baixar / })).toHaveLength(3);
  });

  it("execução publicada sem previews mostra só os downloads, sem buscar as miniaturas", async () => {
    const { api } = setupMedia({ ...withPreviews, artifacts: outputs });

    await screen.findByRole("link", { name: "Baixar thumbnails/férias-10pct.jpg" });
    expect(screen.queryAllByRole("img", { name: PREVIEW_ALT })).toEqual([]);
    expect(downloadsFetched(api.requests)).toBe(false);
  });

  it("não tem violações do axe com as miniaturas", async () => {
    const { container } = setupMedia(withPreviews);
    await screen.findByRole("img", { name: "Pré-visualização de thumbnails/férias-90pct.jpg" });

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
    /** Do preview; a saída fica em `sourceAvailability`. */
    readonly availability?: string;
    readonly sourceAvailability?: string;
  }

  function resultsWithPreview({
    fileName,
    previewName,
    availability = "available",
    sourceAvailability = "available",
  }: Pair) {
    return {
      ...publishedResults,
      artifacts: [
        {
          artifact_id: OUTPUT_ID,
          logical_name: fileName,
          role: "output",
          file_name: fileName,
          availability: sourceAvailability,
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

  it("CSV: a caixa rolável da amostra recebe foco e tem o nome da legenda", async () => {
    setupPreview({ fileName: "clientes.csv", previewName: "clientes.csv" }, () =>
      bytes("nome,email\nAna,ana@example.com\n"),
    );

    const box = await screen.findByRole("region", { name: "Amostra de clientes.csv: 1 registro" });
    expect(box).toHaveAttribute("tabindex", "0");
    expect(within(box).getByRole("table")).toBeInTheDocument();
    expect(await axeViolations(box)).toEqual([]);
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

  it("PDF: a caixa rolável do mapa recebe foco e tem o nome da legenda", async () => {
    setupPreview({ fileName: "selecao.pdf", previewName: "selecao.json" }, () =>
      bytes(
        JSON.stringify({
          schema_version: 1,
          path: "selecao.pdf",
          pages: [{ input_position: 0, page_number: 1 }],
        }),
      ),
    );

    const box = await screen.findByRole("region", { name: "Páginas de selecao.pdf: 1 página" });
    expect(box).toHaveAttribute("tabindex", "0");
    expect(within(box).getByRole("list")).toBeInTheDocument();
    expect(await axeViolations(box)).toEqual([]);
  });

  it("PDF: a legenda com nome longo quebra linha em qualquer ponto", async () => {
    // Sem espaço, o nome só cabe a 320 px quebrando dentro da palavra (WCAG 1.4.10). O jsdom
    // não faz layout, então o teste confere a classe.
    const longName =
      "relatorio_clientes_consolidado_2026_novembro_final_revisado_pela_contabilidade.pdf";
    setupPreview({ fileName: longName, previewName: "selecao.json" }, () =>
      bytes(
        JSON.stringify({
          schema_version: 1,
          path: longName,
          pages: [{ input_position: 0, page_number: 1 }],
        }),
      ),
    );

    const caption = await screen.findByText(`Páginas de ${longName}: 1 página`);
    expect(caption).toHaveClass("wrap-anywhere");
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
    ["missing", "Pré-visualização indisponível: o arquivo se perdeu."],
  ])(
    "preview indisponível (%s) diz o motivo sem pedir o preview",
    async (availability, message) => {
      const { api } = setupPreview({
        fileName: "clientes.csv",
        previewName: "clientes.csv",
        availability,
      });

      expect(await screen.findByText(message)).toBeVisible();
      expect(
        api.requests.some((request) => new URL(request.url).pathname.endsWith("/preview")),
      ).toBe(false);
    },
  );

  it("saída indisponível: o motivo aparece uma vez, na saída, e o preview não é pedido", async () => {
    const packageRef = {
      artifact_id: ARTIFACT_ID,
      logical_name: "pacote",
      role: "package",
      file_name: "resultado.zip",
      availability: "available",
      source_artifact_id: null,
    };
    const pair = resultsWithPreview({
      fileName: "clientes.csv",
      previewName: "clientes.csv",
      availability: "expired",
      sourceAvailability: "expired",
    });
    const { api } = setup(
      () => jsonResponse(runBody({ status: "succeeded" })),
      () => jsonResponse({ ...pair, artifacts: [packageRef, ...pair.artifacts] }),
    );

    expect(await screen.findByRole("listitem")).toHaveTextContent(
      "clientes.csv (arquivo processado): expirou em",
    );
    expect(screen.queryByText(/Pré-visualização indisponível/)).toBeNull();
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
