import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { z } from "zod";

import { createRoutes } from "../app/routes";
import { axeViolations } from "../test/axe";
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
import { renderRoutes } from "../test/renderRoutes";

const csv = (name: string) => new File(["a,b\n1,2\n"], name, { type: "text/csv" });

type RunsHandler = (request: Request) => Response | Promise<Response> | null;

const acceptedRun = () =>
  jsonResponse({ ...runBody(), idempotency_expires_at: "2026-10-15T12:00:00Z" }, 202);

function setup({
  runs = acceptedRun,
  autoResolve = true,
}: { runs?: RunsHandler; autoResolve?: boolean } = {}) {
  const api = fakeApi({
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
  const view = renderRoutes({ routes, path: "/bancada/csv-janitor", client: api.client });
  return { ...view, api, pending, user: userEvent.setup() };
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
  it("abre com o foco no título e o estado vazio da lista", () => {
    setup();

    expect(screen.getByRole("heading", { level: 1, name: "Bancada: CSV Janitor" })).toHaveFocus();
    expect(screen.getByRole("heading", { name: "Nenhum arquivo escolhido" })).toBeInTheDocument();
    expect(screen.getByLabelText("Escolher arquivos")).toHaveAttribute("accept", ".csv,text/csv");
  });

  it("receita desconhecida mostra a página não encontrada", () => {
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

    expect(screen.getByRole("heading", { level: 1, name: "Página não encontrada" })).toBeVisible();
  });

  it("envia os arquivos escolhidos e anuncia quando ficam prontos", async () => {
    const { user } = setup();

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
    const { user, pending } = setup({ autoResolve: false });
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
    const { user, api } = setup();

    await user.click(screen.getByRole("button", { name: "Executar" }));

    const summary = screen.getByRole("alert");
    expect(summary).toHaveFocus();
    await user.click(within(summary).getByRole("link", { name: "Escolha pelo menos um arquivo." }));
    expect(screen.getByLabelText("Escolher arquivos")).toHaveFocus();
    expect(runRequests(api.requests)).toHaveLength(0);
  });

  it("Executar com envio em curso pede para esperar", async () => {
    const { user, pending } = setup({ autoResolve: false });
    await chooseFiles(user, ["a.csv"]);
    await waitFor(() => {
      expect(pending).toHaveLength(1);
    });

    await user.click(screen.getByRole("button", { name: "Executar" }));

    expect(screen.getByRole("alert")).toHaveTextContent("Espere o envio dos arquivos terminar.");
  });

  it("cria a execução na ordem escolhida e abre a tela dela", async () => {
    const { user, api } = setup();
    await chooseFiles(user, ["a.csv", "b.csv"]);
    await waitUntilSent(2);

    await user.click(screen.getByRole("button", { name: "Mover para cima b.csv" }));
    await user.click(screen.getByRole("button", { name: "Executar" }));

    expect(await screen.findByRole("heading", { level: 1, name: "Execução" })).toHaveFocus();
    const [request] = runRequests(api.requests);
    expect(request?.headers.get("Idempotency-Key")).toMatch(/^[0-9a-f-]{36}$/);
    const body = await bodyOf(request);
    expect(body.input_upload_ids).toEqual([uploadIdFor(2), uploadIdFor(1)]);
    expect(body.recipe_id).toBe("csv-janitor");
    expect(body.parameters).toMatchObject({ input_delimiter: ",", output_columns: null });
  });

  it("repetir o mesmo comando reusa a Idempotency-Key; mudar o comando gera outra", async () => {
    let calls = 0;
    const { user, api } = setup({
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
    const { user } = setup({
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
    const { user } = setup({
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
    const { user } = setup({ runs: () => problemResponse(409, "ACTIVE_RUN_LIMIT") });
    await chooseFiles(user, ["a.csv"]);
    await waitUntilSent(1);

    await user.click(screen.getByRole("button", { name: "Executar" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Você já tem uma execução em andamento.");
    expect(alert).not.toHaveTextContent("Detail from server");
    expect(alert).not.toHaveTextContent("{");
  });

  it("arquivo que falhou pode ser enviado de novo ou removido", async () => {
    const { user, pending } = setup({ autoResolve: false });
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
    const { user, container } = setup();
    await chooseFiles(user, ["a.csv"]);
    await waitUntilSent(1);
    await user.click(screen.getByRole("button", { name: "Adicionar regra de caixa" }));
    await user.click(screen.getByRole("button", { name: "Executar" }));

    expect(await axeViolations(container)).toEqual([]);
  });
});
