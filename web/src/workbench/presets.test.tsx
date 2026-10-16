import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { appRoutes } from "../test/appRoutes";
import { axeViolations } from "../test/axe";
import { catalogHandler } from "../test/catalog";
import { fakeApi, jsonResponse, problemResponse, screenRequests } from "../test/fakeApi";
import { presetBody, PRESET_ID } from "../test/presets";
import { renderRoutes } from "../test/renderRoutes";

type Handler = (request: Request) => Response | Promise<Response> | null;

const CSRF = "csrf-da-sessao";

function setup(path = "/bancada/csv-janitor", handlers: Readonly<Record<string, Handler>> = {}) {
  const api = fakeApi(
    {
      "GET /api/v1/catalog": catalogHandler(),
      "POST /api/v1/presets": () => jsonResponse(presetBody(), 201),
      [`GET /api/v1/presets/${PRESET_ID}`]: () => jsonResponse(presetBody()),
      ...handlers,
    },
    { csrfToken: CSRF },
  );
  const view = renderRoutes({ routes: appRoutes(api.client), path, client: api.client });
  return { ...view, api, user: userEvent.setup() };
}

function sent(requests: readonly Request[], route: string): Request[] {
  return screenRequests(requests).filter(
    (request) => `${request.method} ${new URL(request.url).pathname}` === route,
  );
}

async function openSaveDialog(user: ReturnType<typeof userEvent.setup>): Promise<HTMLElement> {
  await user.click(await screen.findByRole("button", { name: "Salvar preset" }));
  return screen.getByRole("dialog", { name: "Salvar os parâmetros como preset" });
}

describe("Bancada: Salvar preset", () => {
  it("abre o diálogo com o foco no nome e salva nome, receita e parâmetros do formulário", async () => {
    const { api, user } = setup();

    const dialog = await openSaveDialog(user);
    const name = within(dialog).getByLabelText("Nome do preset");
    expect(name).toHaveFocus();
    expect(await axeViolations(document.body)).toEqual([]);
    await user.type(name, "Clientes do mês");
    await user.click(within(dialog).getByRole("button", { name: "Salvar preset" }));

    expect(await screen.findByText("Preset Clientes do mês salvo.")).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).toBeNull();
    // O diálogo mora dentro do <form> da Bancada: o clique não pode enviar a execução, que sem
    // arquivos mostraria o resumo de erros.
    expect(screen.queryByText("Corrija os itens abaixo antes de continuar.")).toBeNull();
    expect(sent(api.requests, "POST /api/v1/runs")).toEqual([]);
    const [request] = sent(api.requests, "POST /api/v1/presets");
    expect(request?.headers.get("X-CSRF-Token")).toBe(CSRF);
    expect(await request?.json()).toMatchObject({
      name: "Clientes do mês",
      recipe_id: "csv-janitor",
      recipe_version: 1,
      parameters: expect.objectContaining({ input_delimiter: "," }) as unknown,
    });
  });

  it("nome vazio mostra o erro no campo e não chama a API", async () => {
    const { api, user } = setup();

    const dialog = await openSaveDialog(user);
    await user.click(within(dialog).getByRole("button", { name: "Salvar preset" }));

    expect(within(dialog).getByText(/Dê um nome ao preset\./)).toBeVisible();
    expect(within(dialog).getByLabelText("Nome do preset")).toHaveAttribute("aria-invalid", "true");
    expect(sent(api.requests, "POST /api/v1/presets")).toEqual([]);
  });

  it("nome com mais de 100 caracteres é recusado antes da API", async () => {
    const { api, user } = setup();

    const dialog = await openSaveDialog(user);
    await user.type(within(dialog).getByLabelText("Nome do preset"), "x".repeat(101));
    await user.click(within(dialog).getByRole("button", { name: "Salvar preset" }));

    expect(within(dialog).getByText(/Use até 100 caracteres\./)).toBeVisible();
    expect(sent(api.requests, "POST /api/v1/presets")).toEqual([]);
  });

  it("Enter no nome salva o preset e não executa a Bancada", async () => {
    const { api, user } = setup();

    const dialog = await openSaveDialog(user);
    await user.type(within(dialog).getByLabelText("Nome do preset"), "Rápido{Enter}");

    await waitFor(() => {
      expect(sent(api.requests, "POST /api/v1/presets")).toHaveLength(1);
    });
    expect(sent(api.requests, "POST /api/v1/runs")).toEqual([]);
  });

  it("nome repetido ou limite de presets (409) avisa no diálogo, que continua aberto", async () => {
    const { user } = setup("/bancada/csv-janitor", {
      "POST /api/v1/presets": () => problemResponse(409, "STATE_CONFLICT"),
    });

    const dialog = await openSaveDialog(user);
    await user.type(within(dialog).getByLabelText("Nome do preset"), "Clientes do mês");
    await user.click(within(dialog).getByRole("button", { name: "Salvar preset" }));

    expect(
      await within(dialog).findByText(
        "Já existe um preset com esse nome, ou a conta chegou a 100 presets.",
      ),
    ).toBeVisible();
    expect(screen.getByRole("dialog")).toBeVisible();
  });

  it("com parâmetros inválidos, pede a correção em vez de abrir o diálogo", async () => {
    const { api, user } = setup("/bancada/image-forge");

    const width = await screen.findByLabelText("Largura máxima, em pixels");
    await user.clear(width);
    await user.type(width, "0");
    await user.click(screen.getByRole("button", { name: "Salvar preset" }));

    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByText("Corrija os parâmetros antes de salvar o preset.")).toBeVisible();
    expect(sent(api.requests, "POST /api/v1/presets")).toEqual([]);
  });
});

describe("Bancada aberta com um preset", () => {
  it("pré-preenche os parâmetros e diz de qual preset vieram", async () => {
    setup(`/bancada/csv-janitor?preset=${PRESET_ID}`);

    const notice = await screen.findByText("Parâmetros do preset Clientes do mês.");
    expect(notice).toBeVisible();
    expect(notice).toHaveAttribute("role", "status");
    expect(screen.getByLabelText("Delimitador dos arquivos de saída")).toHaveValue(";");
    expect(screen.getByLabelText("Colunas sem espaços nas pontas")).toHaveValue("nome");
  });

  it("preset de outra receita abre com os parâmetros padrão e avisa", async () => {
    setup(`/bancada/csv-janitor?preset=${PRESET_ID}`, {
      [`GET /api/v1/presets/${PRESET_ID}`]: () =>
        jsonResponse(presetBody({ recipe_id: "image-forge", parameters: {} })),
    });

    expect(
      await screen.findByText(/Este preset é de outra receita ou de uma versão/),
    ).toBeVisible();
    expect(screen.getByLabelText("Delimitador dos arquivos de saída")).toHaveValue(",");
  });

  it("preset de outra versão da receita abre com os parâmetros padrão e avisa", async () => {
    setup(`/bancada/csv-janitor?preset=${PRESET_ID}`, {
      [`GET /api/v1/presets/${PRESET_ID}`]: () => jsonResponse(presetBody({ recipe_version: 2 })),
    });

    expect(
      await screen.findByText(/Este preset é de outra receita ou de uma versão/),
    ).toBeVisible();
    expect(screen.getByLabelText("Delimitador dos arquivos de saída")).toHaveValue(",");
  });

  it("parâmetros num formato que a interface não conhece abrem os padrões e avisam", async () => {
    setup(`/bancada/csv-janitor?preset=${PRESET_ID}`, {
      [`GET /api/v1/presets/${PRESET_ID}`]: () =>
        jsonResponse(presetBody({ parameters: { output_delimiter: 42 } })),
    });

    expect(
      await screen.findByText(/Este preset é de outra receita ou de uma versão/),
    ).toBeVisible();
    expect(screen.getByLabelText("Delimitador dos arquivos de saída")).toHaveValue(",");
  });

  it("preset que não existe mostra a falha e a Bancada com os padrões", async () => {
    setup(`/bancada/csv-janitor?preset=${PRESET_ID}`, {
      [`GET /api/v1/presets/${PRESET_ID}`]: () => problemResponse(404, "NOT_FOUND"),
    });

    expect(await screen.findByRole("alert")).toBeVisible();
    expect(screen.getByLabelText("Escolher arquivos")).toBeInTheDocument();
  });

  it("id que não é UUID é ignorado, sem consultar a API", async () => {
    const { api } = setup("/bancada/csv-janitor?preset=nao-e-uuid");

    expect(await screen.findByLabelText("Escolher arquivos")).toBeInTheDocument();
    expect(
      screenRequests(api.requests).filter((request) => request.url.includes("/presets")),
    ).toEqual([]);
  });
});
