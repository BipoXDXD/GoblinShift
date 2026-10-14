import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { z } from "zod";

import { createRoutes } from "../app/routes";
import { axeViolations } from "../test/axe";
import { catalogHandler, catalogVersion, withVersion } from "../test/catalog";
import {
  fakeApi,
  jsonResponse,
  problemResponse,
  runBody,
  RUN_ID,
  uploadIdFor,
  uploadIntentsHandler,
} from "../test/fakeApi";
import { fakeUploader } from "../test/fakeUploader";
import { expectHeadingFocused } from "../test/focus";
import { renderRoutes } from "../test/renderRoutes";

const csv = (name: string) => new File(["a,b\n1,2\n"], name, { type: "text/csv" });

type RunsHandler = (request: Request) => Response | Promise<Response> | null;

const acceptedRun = () =>
  jsonResponse({ ...runBody(), idempotency_expires_at: "2026-10-15T12:00:00Z" }, 202);

interface SetupOptions {
  readonly runs?: RunsHandler;
  readonly autoResolve?: boolean;
  readonly recipe?: string;
  readonly catalog?: () => Response | null;
}

function renderWorkbench({
  runs = acceptedRun,
  autoResolve = true,
  recipe = "csv-janitor",
  catalog = catalogHandler(),
}: SetupOptions = {}) {
  const api = fakeApi({
    "GET /api/v1/catalog": catalog,
    "POST /api/v1/uploads": uploadIntentsHandler(),
    "POST /api/v1/runs": runs,
    [`GET /api/v1/runs/${RUN_ID}`]: () => jsonResponse(runBody()),
  });
  const { uploader, pending } = fakeUploader({ autoResolve });
  const routes = createRoutes({
    uploadServices: {
      client: api.client,
      uploader,
      rateLimit: { maxAttempts: 1, fallbackDelayMs: 0, random: () => 0 },
    },
  });
  const view = renderRoutes({ routes, path: `/bancada/${recipe}`, client: api.client });
  return { ...view, api, pending, user: userEvent.setup() };
}

/** Bancada já com o catálogo carregado e o formulário na tela. */
async function setup(options: SetupOptions = {}) {
  const view = renderWorkbench(options);
  await screen.findByLabelText("Escolher arquivos");
  return view;
}

async function chooseFiles(user: ReturnType<typeof userEvent.setup>, names: readonly string[]) {
  await user.upload(screen.getByLabelText("Escolher arquivos"), names.map(csv));
}

async function waitUntilSent(count: number) {
  await waitFor(() => {
    expect(screen.getAllByText("Enviado")).toHaveLength(count);
  });
}

function runRequests(requests: readonly Request[]): Request[] {
  return requests.filter((request) => new URL(request.url).pathname === "/api/v1/runs");
}

const sentCommand = z.object({
  recipe_id: z.string(),
  recipe_version: z.number(),
  input_upload_ids: z.array(z.string()),
  parameters: z.record(z.string(), z.unknown()),
});

async function bodyOf(request: Request | undefined) {
  if (request === undefined) {
    throw new Error("POST /runs não foi feito");
  }
  return sentCommand.parse(await request.json());
}

describe("Bancada", () => {
  it("abre com o foco no título e o estado vazio da lista", async () => {
    await setup();

    await expectHeadingFocused("Bancada: CSV Janitor");
    expect(screen.getByRole("heading", { name: "A bancada está vazia" })).toBeInTheDocument();
    expect(screen.getByLabelText("Escolher arquivos")).toHaveAttribute("accept", ".csv,text/csv");
  });

  it("a ordem do Tab segue a leitura nas duas colunas: arquivos, parâmetros, Executar", async () => {
    const { user } = await setup();
    const files = screen.getByLabelText("Escolher arquivos");
    const firstParameter = screen.getByLabelText("Delimitador dos arquivos de entrada");
    const run = screen.getByRole("button", { name: "Executar" });

    files.focus();
    await user.tab();

    expect(firstParameter).toHaveFocus();
    expect(files.compareDocumentPosition(run) & Node.DOCUMENT_POSITION_FOLLOWING).toBe(
      Node.DOCUMENT_POSITION_FOLLOWING,
    );
    expect(firstParameter.compareDocumentPosition(run) & Node.DOCUMENT_POSITION_FOLLOWING).toBe(
      Node.DOCUMENT_POSITION_FOLLOWING,
    );
  });

  it("diz quantos arquivos estão escolhidos, em pt-BR", async () => {
    const { user } = await setup();
    expect(screen.getByText("Nenhum arquivo escolhido")).toBeVisible();

    await chooseFiles(user, ["a.csv", "b.csv"]);

    expect(await screen.findByText("2 arquivos escolhidos")).toBeVisible();
  });

  it("diz quantos arquivos e quantos bytes a receita aceita", async () => {
    await setup();

    expect(screen.getByLabelText("Escolher arquivos")).toHaveAccessibleDescription(
      /Até \d+ arquivos por execução/,
    );
  });

  it("receita desconhecida mostra a página não encontrada", async () => {
    const api = fakeApi({});
    const { uploader } = fakeUploader({ autoResolve: true });
    renderRoutes({
      routes: createRoutes({
        uploadServices: {
          client: api.client,
          uploader,
          rateLimit: { maxAttempts: 1, fallbackDelayMs: 0, random: () => 0 },
        },
      }),
      path: "/bancada/receita-que-nao-existe",
      client: api.client,
    });

    expect(
      await screen.findByRole("heading", { level: 1, name: "Página não encontrada" }),
    ).toBeVisible();
  });

  it("envia os arquivos escolhidos e anuncia quando ficam prontos", async () => {
    const { user } = await setup();

    await chooseFiles(user, ["clientes.csv"]);

    await waitUntilSent(1);
    expect(screen.getByRole("list", { name: "Ordem de processamento" })).toHaveTextContent(
      "clientes.csv",
    );
    expect(
      screen
        .getAllByRole("status")
        .some((region) => region.textContent === "clientes.csv enviado."),
    ).toBe(true);
  });

  it("mostra o progresso real do envio", async () => {
    const { user, pending } = await setup({ autoResolve: false });
    await chooseFiles(user, ["clientes.csv"]);
    await waitFor(() => {
      expect(pending).toHaveLength(1);
    });

    act(() => {
      pending[0]?.options.onProgress({ phase: "sending", loadedBytes: 4, totalBytes: 8 });
    });

    const progress = screen.getByRole("progressbar", { name: "Envio de clientes.csv" });
    expect(progress).toHaveAttribute("value", "4");
    expect(progress).toHaveAttribute("max", "8");
    expect(screen.getByText("Enviando: 50%")).toBeInTheDocument();
  });

  it("Executar sem arquivos foca o resumo de erros e não chama a API", async () => {
    const { user, api } = await setup();

    await user.click(screen.getByRole("button", { name: "Executar" }));

    const summary = screen.getByRole("alert");
    expect(summary).toHaveFocus();
    await user.click(within(summary).getByRole("link", { name: "Escolha pelo menos um arquivo." }));
    expect(screen.getByLabelText("Escolher arquivos")).toHaveFocus();
    expect(runRequests(api.requests)).toHaveLength(0);
  });

  it("Executar com envio em curso pede para esperar", async () => {
    const { user, pending } = await setup({ autoResolve: false });
    await chooseFiles(user, ["a.csv"]);
    await waitFor(() => {
      expect(pending).toHaveLength(1);
    });

    await user.click(screen.getByRole("button", { name: "Executar" }));

    expect(screen.getByRole("alert")).toHaveTextContent("Espere o envio dos arquivos terminar.");
  });

  it("cria a execução na ordem escolhida e abre a tela dela", async () => {
    const { user, api } = await setup();
    await chooseFiles(user, ["a.csv", "b.csv"]);
    await waitUntilSent(2);

    await user.click(screen.getByRole("button", { name: "Mover para cima b.csv" }));
    await user.click(screen.getByRole("button", { name: "Executar" }));

    await expectHeadingFocused("Execução");
    const [request] = runRequests(api.requests);
    expect(request?.headers.get("Idempotency-Key")).toMatch(/^[0-9a-f-]{36}$/);
    const body = await bodyOf(request);
    expect(body.input_upload_ids).toEqual([uploadIdFor(2), uploadIdFor(1)]);
    expect(body.recipe_id).toBe("csv-janitor");
    expect(body.parameters).toMatchObject({ input_delimiter: ",", output_columns: null });
  });

  it("repetir o mesmo comando reusa a Idempotency-Key; mudar o comando gera outra", async () => {
    let calls = 0;
    const { user, api } = await setup({
      runs: () => {
        calls += 1;
        return calls < 3 ? problemResponse(503, "SERVICE_UNAVAILABLE") : acceptedRun();
      },
    });
    await chooseFiles(user, ["a.csv"]);
    await waitUntilSent(1);

    await user.click(screen.getByRole("button", { name: "Executar" }));
    await screen.findByText(/O serviço está indisponível/);
    await user.click(screen.getByRole("button", { name: "Executar" }));
    await waitFor(() => {
      expect(runRequests(api.requests)).toHaveLength(2);
    });
    await user.selectOptions(
      screen.getByRole("combobox", { name: "Delimitador dos arquivos de saída" }),
      ";",
    );
    await user.click(screen.getByRole("button", { name: "Executar" }));
    await screen.findByRole("heading", { level: 1, name: "Execução" });

    const keys = runRequests(api.requests).map((request) => request.headers.get("Idempotency-Key"));
    expect(keys[0]).toBe(keys[1]);
    expect(keys[2]).not.toBe(keys[0]);
  });

  it("erros por campo do servidor aparecem no campo, com o texto do servidor", async () => {
    const { user } = await setup({
      runs: () =>
        problemResponse(422, "VALIDATION_FAILED", {
          errors: [
            {
              field: "body.parameters.output_columns",
              code: "value_error",
              message: "a coluna 'x' não existe",
            },
            { field: "body.recipe_version", code: "value_error", message: "versão recusada" },
          ],
        }),
    });
    await chooseFiles(user, ["a.csv"]);
    await waitUntilSent(1);

    await user.click(screen.getByRole("button", { name: "Executar" }));

    const summary = await screen.findByRole("alert");
    await waitFor(() => {
      expect(summary).toHaveFocus();
    });
    expect(within(summary).getByRole("link", { name: "a coluna 'x' não existe" })).toBeVisible();
    expect(summary).toHaveTextContent("versão recusada");
    expect(
      screen.getByRole("textbox", { name: "Colunas finais, na ordem" }),
    ).toHaveAccessibleDescription(/Erro: a coluna 'x' não existe/);
  });

  it("erro por campo fora das regras da receita ganha texto próprio pelo código", async () => {
    const { user } = await setup({
      runs: () =>
        problemResponse(422, "VALIDATION_FAILED", {
          errors: [
            {
              field: "body.parameters.formula_guard",
              code: "enum",
              message: "Input should be 'none' or 'tab_prefix'",
            },
            {
              field: "body.parameters.output_columns",
              code: "value_error",
              message: "Value error, repete colunas: ['x']",
            },
          ],
        }),
    });
    await chooseFiles(user, ["a.csv"]);
    await waitUntilSent(1);

    await user.click(screen.getByRole("button", { name: "Executar" }));

    const summary = await screen.findByRole("alert");
    expect(summary).toHaveTextContent("Escolha uma das opções.");
    expect(summary).toHaveTextContent("repete colunas: ['x']");
    expect(summary).not.toHaveTextContent("Input should");
    expect(summary).not.toHaveTextContent("Value error");
  });

  it("recusa do servidor aparece com texto próprio, nunca o problem+json", async () => {
    const { user } = await setup({ runs: () => problemResponse(409, "ACTIVE_RUN_LIMIT") });
    await chooseFiles(user, ["a.csv"]);
    await waitUntilSent(1);

    await user.click(screen.getByRole("button", { name: "Executar" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Você já tem uma execução em andamento.");
    expect(alert).not.toHaveTextContent("Detail from server");
    expect(alert).not.toHaveTextContent("{");
  });

  it("arquivo que falhou pode ser enviado de novo ou removido", async () => {
    const { user, pending } = await setup({ autoResolve: false });
    await chooseFiles(user, ["a.csv", "b.csv"]);
    await waitFor(() => {
      expect(pending).toHaveLength(2);
    });

    act(() => {
      pending[0]?.reject(new Error("falhou"));
    });

    expect(await screen.findByRole("button", { name: "Enviar de novo a.csv" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Remover a.csv" }));
    expect(screen.getByRole("list", { name: "Ordem de processamento" })).not.toHaveTextContent(
      "a.csv",
    );
    expect(screen.getByLabelText("Escolher arquivos")).toHaveFocus();
  });

  it("não tem violações do axe com arquivos e erros na tela", async () => {
    const { user, container } = await setup();
    await chooseFiles(user, ["a.csv"]);
    await waitUntilSent(1);
    await user.click(screen.getByRole("button", { name: "Adicionar regra de caixa" }));
    await user.click(screen.getByRole("button", { name: "Executar" }));

    expect(await axeViolations(container)).toEqual([]);
  });
  it("mostra que está carregando a receita enquanto o catálogo não chega", async () => {
    renderWorkbench({ catalog: () => null });

    await expectHeadingFocused("Bancada: CSV Janitor");
    expect(screen.getByText("Carregando a receita.")).toBeInTheDocument();
    expect(screen.queryByLabelText("Escolher arquivos")).toBeNull();
  });

  it("falha do catálogo aparece com texto próprio e sem formulário", async () => {
    renderWorkbench({ catalog: () => problemResponse(503, "SERVICE_UNAVAILABLE") });

    expect(await screen.findByRole("alert")).toHaveTextContent(/O serviço está indisponível/);
    expect(screen.queryByLabelText("Escolher arquivos")).toBeNull();
  });

  it.each([
    ["sem a versão do formulário", withVersion("csv-janitor", null)],
    [
      "com a versão num formato desconhecido",
      withVersion("csv-janitor", { ...catalogVersion("csv-janitor"), defaults: {} }),
    ],
  ])("catálogo %s: receita indisponível, sem formulário", async (_, body) => {
    renderWorkbench({ catalog: catalogHandler(body) });

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Esta receita não está disponível no momento.",
    );
    expect(screen.queryByLabelText("Escolher arquivos")).toBeNull();
  });

  it("o máximo de arquivos vem do catálogo", async () => {
    const { user, api } = await setup();
    await chooseFiles(user, ["1.csv", "2.csv", "3.csv", "4.csv", "5.csv", "6.csv"]);
    await waitUntilSent(6);

    await user.click(screen.getByRole("button", { name: "Executar" }));

    expect(screen.getByRole("alert")).toHaveTextContent("Use no máximo 5 arquivos.");
    expect(runRequests(api.requests)).toHaveLength(0);
  });

  it("os defaults do formulário vêm do catálogo", async () => {
    const version = catalogVersion("csv-janitor");
    const { user, api } = await setup({
      catalog: catalogHandler(
        withVersion("csv-janitor", {
          ...version,
          defaults: { ...version.defaults, output_delimiter: ";" },
        }),
      ),
    });
    await chooseFiles(user, ["a.csv"]);
    await waitUntilSent(1);

    await user.click(screen.getByRole("button", { name: "Executar" }));

    await screen.findByRole("heading", { level: 1, name: "Execução" });
    expect((await bodyOf(runRequests(api.requests)[0])).parameters).toMatchObject({
      output_delimiter: ";",
    });
  });

  it("Image Forge: aceita imagens e cria a execução com os parâmetros do formulário", async () => {
    const { user, api } = await setup({ recipe: "image-forge" });
    expect(screen.getByRole("heading", { level: 1, name: "Bancada: Image Forge" })).toHaveFocus();
    expect(screen.getByLabelText("Escolher arquivos")).toHaveAttribute(
      "accept",
      ".jpg,.jpeg,.png,.mpo,image/jpeg,image/png",
    );
    await user.upload(screen.getByLabelText("Escolher arquivos"), [
      new File(["png"], "foto.png", { type: "image/png" }),
    ]);
    await waitUntilSent(1);
    await user.click(screen.getByRole("checkbox", { name: "Gerar miniaturas" }));

    await user.click(screen.getByRole("button", { name: "Executar" }));

    await screen.findByRole("heading", { level: 1, name: "Execução" });
    const body = await bodyOf(runRequests(api.requests)[0]);
    expect(body).toMatchObject({ recipe_id: "image-forge", recipe_version: 1 });
    expect(body.parameters).toEqual({
      max_width: 1600,
      max_height: 1600,
      output_format: "preserve",
      jpeg_quality: 85,
      jpeg_background: "#FFFFFF",
      make_thumbnails: true,
    });
  });

  it("Image Forge: erro do servidor no campo aparece no campo", async () => {
    const { user } = await setup({
      recipe: "image-forge",
      runs: () =>
        problemResponse(422, "VALIDATION_FAILED", {
          errors: [
            {
              field: "body.parameters.max_width",
              code: "less_than_equal",
              message: "Input should be less than or equal to 4096",
            },
          ],
        }),
    });
    await user.upload(screen.getByLabelText("Escolher arquivos"), [new File(["x"], "a.png")]);
    await waitUntilSent(1);

    await user.click(screen.getByRole("button", { name: "Executar" }));

    await screen.findByRole("link", { name: "O valor passa do máximo permitido." });
    expect(
      screen.getByRole("textbox", { name: "Largura máxima, em pixels" }),
    ).toHaveAccessibleDescription(/Erro: O valor passa do máximo permitido\./);
  });

  it("Downloads Detox: aceita qualquer tipo e respeita o teto de bytes do catálogo", async () => {
    const version = catalogVersion("downloads-detox");
    const { user, api } = await setup({
      recipe: "downloads-detox",
      catalog: catalogHandler(
        withVersion("downloads-detox", {
          ...version,
          limits: { ...version.limits, max_total_input_bytes: 4 },
        }),
      ),
    });
    expect(screen.getByLabelText("Escolher arquivos")).not.toHaveAttribute("accept");
    await user.upload(screen.getByLabelText("Escolher arquivos"), [new File(["12345"], "a.zip")]);
    await waitUntilSent(1);

    await user.click(screen.getByRole("button", { name: "Executar" }));

    expect(screen.getByRole("alert")).toHaveTextContent(
      "Os arquivos somam 5 bytes; o máximo por execução é 4 bytes.",
    );
    expect(runRequests(api.requests)).toHaveLength(0);
  });

  it("Downloads Detox: cria a execução sem prefixo quando os nomes são preservados", async () => {
    const { user, api } = await setup({ recipe: "downloads-detox" });
    await user.upload(screen.getByLabelText("Escolher arquivos"), [new File(["x"], "a.txt")]);
    await waitUntilSent(1);

    await user.click(screen.getByRole("button", { name: "Executar" }));

    await screen.findByRole("heading", { level: 1, name: "Execução" });
    expect(await bodyOf(runRequests(api.requests)[0])).toMatchObject({
      recipe_id: "downloads-detox",
      parameters: { deduplicate: true, group_by: "kind", name_mode: "preserve" },
    });
  });
});
