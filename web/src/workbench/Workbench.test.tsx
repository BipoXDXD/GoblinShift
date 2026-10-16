import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { z } from "zod";

import { appRoutes } from "../test/appRoutes";
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
  const routes = appRoutes(api.client, { uploader });
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
    // Rotação estável pela receita: a CSV Janitor cai sempre na Jade.
    expect(screen.getByRole("img", { name: /^Jade, / })).toBeInTheDocument();
    expect(screen.getByLabelText("Escolher arquivos")).toHaveAttribute("accept", ".csv,text/csv");
  });

  it("outra receita pode cair na outra goblin do vazio, e sempre na mesma", async () => {
    await setup({ recipe: "image-forge" });

    expect(screen.getByRole("img", { name: /^Lua, / })).toBeInTheDocument();
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

  it.each(["csv-janitor", "image-forge", "downloads-detox"])(
    "o nome Parâmetros aparece uma vez só na Bancada de %s",
    async (recipe) => {
      await setup({ recipe });

      const named = [
        ...screen.queryAllByRole("heading", { name: /Parâmetros/ }),
        ...screen.queryAllByRole("group", { name: /Parâmetros/ }),
      ];
      expect(named.map((element) => element.textContent)).toEqual(["Parâmetros"]);
    },
  );

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
    renderRoutes({
      routes: appRoutes(api.client),
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
      pending[1]?.reject(new Error("falhou"));
    });

    expect(await screen.findByRole("button", { name: "Enviar de novo b.csv" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Remover b.csv" }));
    const dialog = screen.getByRole("dialog", { name: "Remover b.csv?" });
    expect(within(dialog).getByRole("button", { name: "Cancelar" })).toHaveFocus();
    await user.click(within(dialog).getByRole("button", { name: "Remover" }));

    const list = screen.getByRole("list", { name: "Ordem de processamento" });
    expect(list).not.toHaveTextContent("b.csv");
    expect(list).toHaveTextContent("a.csv");
    expect(screen.getByLabelText("Escolher arquivos")).toHaveFocus();
  });

  it("cancelar a remoção mantém o arquivo", async () => {
    const { user } = await setup();
    await chooseFiles(user, ["a.csv"]);
    await waitUntilSent(1);

    await user.click(screen.getByRole("button", { name: "Remover a.csv" }));
    await user.click(screen.getByRole("button", { name: "Cancelar" }));

    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByRole("list", { name: "Ordem de processamento" })).toHaveTextContent("a.csv");
    expect(screen.getByRole("button", { name: "Remover a.csv" })).toHaveFocus();
  });

  it("remover durante o envio aborta o envio sem deixar erro", async () => {
    const { user, pending } = await setup({ autoResolve: false });
    await chooseFiles(user, ["a.csv"]);
    await waitFor(() => {
      expect(pending).toHaveLength(1);
    });

    await user.click(screen.getByRole("button", { name: "Remover a.csv" }));
    await user.click(screen.getByRole("button", { name: "Remover" }));

    expect(screen.queryByText("a.csv")).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("o segundo clique em Remover, com o diálogo já fechado, não tira outro arquivo", async () => {
    const { user } = await setup();
    await chooseFiles(user, ["a.csv", "b.csv", "c.csv"]);
    await waitUntilSent(3);

    await user.click(screen.getByRole("button", { name: "Remover b.csv" }));
    const confirm = screen.getByRole("button", { name: "Remover" });
    await user.dblClick(confirm);

    const list = screen.getByRole("list", { name: "Ordem de processamento" });
    expect(list).not.toHaveTextContent("b.csv");
    expect(list).toHaveTextContent("a.csv");
    expect(list).toHaveTextContent("c.csv");
  });

  it("com 2 arquivos ou mais, Remover todos pede confirmação com a contagem", async () => {
    const { user } = await setup();
    await chooseFiles(user, ["a.csv", "b.csv"]);
    await waitUntilSent(2);

    await user.click(screen.getByRole("button", { name: "Remover todos" }));
    const dialog = screen.getByRole("dialog", { name: "Remover os 2 arquivos?" });
    await user.click(within(dialog).getByRole("button", { name: "Remover todos" }));

    expect(screen.queryByRole("list", { name: "Ordem de processamento" })).toBeNull();
    expect(screen.getByLabelText("Escolher arquivos")).toHaveFocus();
  });

  it("a contagem do Remover todos não muda com o diálogo aberto", async () => {
    const { user } = await setup();
    await chooseFiles(user, ["a.csv", "b.csv", "c.csv"]);
    await waitUntilSent(3);

    await user.click(screen.getByRole("button", { name: "Remover todos" }));

    expect(screen.getByRole("dialog", { name: "Remover os 3 arquivos?" })).toBeVisible();
    // A lista muda por baixo do diálogo (o dublê do jsdom não deixa a página inerte): o título
    // promete o que foi visto ao abrir, não o estado de agora.
    await chooseFiles(user, ["d.csv"]);
    await waitUntilSent(4);

    expect(screen.getByRole("dialog", { name: "Remover os 3 arquivos?" })).toBeVisible();
  });

  it("com 1 arquivo, não há Remover todos", async () => {
    const { user } = await setup();
    await chooseFiles(user, ["a.csv", "b.csv"]);
    await waitUntilSent(2);
    await user.click(screen.getByRole("button", { name: "Remover a.csv" }));
    await user.click(screen.getByRole("button", { name: "Remover" }));

    expect(screen.queryByRole("button", { name: "Remover todos" })).toBeNull();
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

  it("sem login, o 401 do catálogo vira o convite para entrar, sem erro nem formulário", async () => {
    renderWorkbench({ catalog: () => problemResponse(401, "AUTHENTICATION_REQUIRED") });

    const notice = await screen.findByRole("region", { name: "Entre para continuar" });
    expect(within(notice).getByRole("link", { name: "Entrar com GitHub" })).toHaveAttribute(
      "href",
      "/auth/login/github?return_to=%2Fbancada%2Fcsv-janitor",
    );
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.queryByLabelText("Escolher arquivos")).toBeNull();
    expect(screen.getByRole("heading", { level: 1, name: "Bancada: CSV Janitor" })).toBeVisible();
  });

  it("login antigo demais pede para entrar de novo, com reauth", async () => {
    renderWorkbench({ catalog: () => problemResponse(401, "REAUTH_REQUIRED") });

    const notice = await screen.findByRole("region", { name: "Entre de novo para confirmar" });
    expect(within(notice).getByRole("link", { name: "Entrar com Google" })).toHaveAttribute(
      "href",
      "/auth/login/google?return_to=%2Fbancada%2Fcsv-janitor&reauth=1",
    );
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("sem o aceite dos termos, leva ao aceite e volta para a Bancada", async () => {
    renderWorkbench({ catalog: () => problemResponse(403, "TERMS_NOT_ACCEPTED") });

    const notice = await screen.findByRole("region", { name: "Aceite os termos para continuar" });
    expect(within(notice).getByRole("link", { name: "Ler e aceitar os termos" })).toHaveAttribute(
      "href",
      "/termos/aceitar?volta=%2Fbancada%2Fcsv-janitor",
    );
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

  it("Media Mill: aceita só vídeos da allowlist e diz o teto por arquivo", async () => {
    await setup({ recipe: "media-mill" });

    const input = screen.getByLabelText("Escolher arquivos");
    expect(input).toHaveAttribute("accept", ".mp4,.mov,.webm,video/mp4,video/quicktime,video/webm");
    expect(input).toHaveAccessibleDescription(
      /Até 5 arquivos por execução, somando no máximo 536,9 MB\. Cada arquivo com até 268,4 MB\./,
    );
  });

  it("Media Mill: recusa no cliente o vídeo acima do teto por arquivo", async () => {
    const version = catalogVersion("media-mill");
    const { user, api } = await setup({
      recipe: "media-mill",
      catalog: catalogHandler(
        withVersion("media-mill", {
          ...version,
          limits: { ...version.limits, max_input_bytes: 4 },
        }),
      ),
    });
    await user.upload(screen.getByLabelText("Escolher arquivos"), [
      new File(["12345"], "ferias.mp4", { type: "video/mp4" }),
    ]);
    await waitUntilSent(1);

    await user.click(screen.getByRole("button", { name: "Executar" }));

    expect(screen.getByRole("alert")).toHaveTextContent(
      "ferias.mp4 tem 5 bytes; o máximo por arquivo nesta receita é 4 bytes.",
    );
    expect(runRequests(api.requests)).toHaveLength(0);
  });

  it("Media Mill: cria a execução com os parâmetros explícitos e nenhum vídeo na tela", async () => {
    const { user, api, container } = await setup({ recipe: "media-mill" });
    await user.upload(screen.getByLabelText("Escolher arquivos"), [
      new File(["x"], "ferias.mp4", { type: "video/mp4" }),
    ]);
    await waitUntilSent(1);
    await user.click(screen.getByRole("radio", { name: "FLAC (compressão sem perdas)" }));
    await user.click(screen.getByRole("radio", { name: "Três, em 10%, 50% e 90% da duração" }));
    expect(container.querySelector("video, audio, img[src^='blob:']")).toBeNull();

    await user.click(screen.getByRole("button", { name: "Executar" }));

    await screen.findByRole("heading", { level: 1, name: "Execução" });
    expect(await bodyOf(runRequests(api.requests)[0])).toMatchObject({
      recipe_id: "media-mill",
      parameters: {
        extract_audio: true,
        audio_format: "flac",
        audio_stream_index: null,
        thumbnail_count: 3,
      },
    });
  });

  it("Media Mill: sem nenhuma saída, o resumo aponta a extração e nada é enviado", async () => {
    const { user, api } = await setup({ recipe: "media-mill" });
    await user.upload(screen.getByLabelText("Escolher arquivos"), [
      new File(["x"], "ferias.mp4", { type: "video/mp4" }),
    ]);
    await waitUntilSent(1);
    await user.click(screen.getByRole("checkbox", { name: "Extrair o áudio" }));
    await user.click(screen.getByRole("radio", { name: "Nenhuma" }));

    await user.click(screen.getByRole("button", { name: "Executar" }));

    expect(await screen.findByRole("link", { name: /Peça ao menos uma saída/ })).toHaveAttribute(
      "href",
      "#media-mill-extract_audio",
    );
    expect(runRequests(api.requests)).toHaveLength(0);
  });
});
