import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ANNOUNCE_DELAY_MS } from "../components/a11y/LiveRegion";
import { untilFakeClock, installAnnounceClock } from "../test/announceClock";
import { appRoutes, preloadRoutes } from "../test/appRoutes";
import { axeViolations } from "../test/axe";
import { catalogHandler } from "../test/catalog";
import {
  fakeApi,
  jsonResponse,
  problemResponse,
  runBody,
  RUN_ID,
  screenRequests,
  uploadIntentsHandler,
} from "../test/fakeApi";
import { presetBody, PRESET_ID } from "../test/presets";
import { escapeIsBlocked, forceClose } from "../test/dialog";
import { recordDescriptionOnFocus } from "../test/focus";
import { renderRoutes } from "../test/renderRoutes";

await preloadRoutes("/", "/bancada/csv-janitor", "/bancada/image-forge");

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

/** As regiões `role="status"` cujo texto é exatamente este. */
function statusWith(text: string): HTMLElement[] {
  return screen.getAllByRole("status").filter((region) => region.textContent === text);
}

async function openSaveDialog(user: ReturnType<typeof userEvent.setup>): Promise<HTMLElement> {
  await user.click(await screen.findByRole("button", { name: "Salvar preset" }));
  return screen.getByRole("dialog", { name: "Salvar os parâmetros como preset" });
}

const NAME_REJECTED = (): Response =>
  problemResponse(422, "VALIDATION_FAILED", {
    errors: [
      {
        field: "body.name",
        code: "value_error",
        message: "o nome não aceita caracteres de controle nem de formato invisível",
      },
    ],
  });

describe("Bancada: Salvar preset", () => {
  it("abre o diálogo com o foco no nome e salva nome, receita e parâmetros do formulário", async () => {
    const { api, user } = setup();

    const dialog = await openSaveDialog(user);
    const name = within(dialog).getByLabelText("Nome do preset");
    expect(name).toHaveFocus();
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

  it("aberto, o diálogo não tem violações do axe", async () => {
    const { user } = setup();

    await openSaveDialog(user);

    expect(await axeViolations(document.body)).toEqual([]);
  });

  it("nome vazio mostra o erro no campo e não chama a API", async () => {
    const { api, user } = setup();

    const dialog = await openSaveDialog(user);
    await user.click(within(dialog).getByRole("button", { name: "Salvar preset" }));

    expect(within(dialog).getByText(/Dê um nome ao preset\./)).toBeVisible();
    expect(within(dialog).getByLabelText("Nome do preset")).toHaveAttribute("aria-invalid", "true");
    expect(sent(api.requests, "POST /api/v1/presets")).toEqual([]);
  });

  it("nome vazio leva o foco ao campo, que anuncia o erro pela descrição", async () => {
    const { user } = setup();

    const dialog = await openSaveDialog(user);
    const name = within(dialog).getByLabelText("Nome do preset");
    const atFocus = recordDescriptionOnFocus(name);
    await user.click(within(dialog).getByRole("button", { name: "Salvar preset" }));

    expect(name).toHaveFocus();
    // O erro já está no DOM e ligado ao campo quando o foco chega, e não só depois do render.
    expect(atFocus.descriptions).toEqual([
      expect.stringContaining("Dê um nome ao preset.") as unknown,
    ]);
    expect(name).toHaveAccessibleDescription(/Erro: Dê um nome ao preset\./);
  });

  it("nome com mais de 100 caracteres é recusado antes da API", async () => {
    const { api, user } = setup();

    const dialog = await openSaveDialog(user);
    const name = within(dialog).getByLabelText("Nome do preset");
    // Colado de uma vez: 101 teclas com o user.type levam ~2 s e já estouraram o tempo do teste.
    await user.click(name);
    await user.paste("x".repeat(101));
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

  it("nome de 100 caracteres contados como o servidor (code points após NFC) é aceito", async () => {
    const { api, user } = setup();

    const dialog = await openSaveDialog(user);
    // 60 emojis são 120 unidades UTF-16; "e" + acento combinado vira um só caractere no NFC.
    await user.click(within(dialog).getByLabelText("Nome do preset"));
    await user.paste("🧌".repeat(60) + "e\u0301".repeat(40));
    await user.click(within(dialog).getByRole("button", { name: "Salvar preset" }));

    await waitFor(() => {
      expect(sent(api.requests, "POST /api/v1/presets")).toHaveLength(1);
    });
    expect(within(dialog).queryByText(/Use até 100 caracteres\./)).toBeNull();
  });

  it("nome colado em NFD vai em NFC, a forma que o servidor mede", async () => {
    const { api, user } = setup();

    const dialog = await openSaveDialog(user);
    // 120 code points em NFD (acima do teto de 100 do servidor); 60 depois do NFC.
    await user.click(within(dialog).getByLabelText("Nome do preset"));
    await user.paste("é".repeat(60));
    await user.click(within(dialog).getByRole("button", { name: "Salvar preset" }));

    await waitFor(() => {
      expect(sent(api.requests, "POST /api/v1/presets")).toHaveLength(1);
    });
    const [request] = sent(api.requests, "POST /api/v1/presets");
    expect(await request?.json()).toMatchObject({ name: "é".repeat(60) });
  });

  it("101 code points são recusados mesmo com menos de 101 unidades por caractere", async () => {
    const { api, user } = setup();

    const dialog = await openSaveDialog(user);
    await user.click(within(dialog).getByLabelText("Nome do preset"));
    await user.paste("🧌".repeat(101));
    await user.click(within(dialog).getByRole("button", { name: "Salvar preset" }));

    expect(within(dialog).getByText(/Use até 100 caracteres\./)).toBeVisible();
    expect(sent(api.requests, "POST /api/v1/presets")).toEqual([]);
  });

  it("Cancelar devolve o foco a Salvar preset", async () => {
    const { user } = setup();
    const dialog = await openSaveDialog(user);

    await user.click(within(dialog).getByRole("button", { name: "Cancelar" }));

    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByRole("button", { name: "Salvar preset" })).toHaveFocus();
  });

  it("fechado pelo navegador (Esc), devolve o foco a Salvar preset", async () => {
    const { user } = setup();
    const dialog = await openSaveDialog(user);

    forceClose(dialog);

    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByRole("button", { name: "Salvar preset" })).toHaveFocus();
  });

  it("fechado pelo navegador com o pedido em voo, reabre e mostra o 409 que chegou depois", async () => {
    let answer: (response: Response) => void = () => undefined;
    const { user } = setup("/bancada/csv-janitor", {
      "POST /api/v1/presets": () =>
        new Promise<Response>((resolve) => {
          answer = resolve;
        }),
    });

    const dialog = await openSaveDialog(user);
    await user.type(within(dialog).getByLabelText("Nome do preset"), "Clientes do mês");
    await user.click(within(dialog).getByRole("button", { name: "Salvar preset" }));
    await within(dialog).findByRole("button", { name: "Salvando…" });
    expect(escapeIsBlocked(dialog)).toBe(true);
    forceClose(dialog);
    expect(screen.queryByRole("dialog")).toBeNull();
    answer(problemResponse(409, "STATE_CONFLICT"));

    await user.click(screen.getByRole("button", { name: "Salvar preset" }));
    const reopened = screen.getByRole("dialog", { name: "Salvar os parâmetros como preset" });
    expect(
      await within(reopened).findByText(
        "Já existe um preset com esse nome, ou a conta chegou a 100 presets.",
      ),
    ).toBeVisible();
  });

  it("nome recusado pelo servidor mostra o erro no campo, com foco, sem o alerta geral", async () => {
    const { user } = setup("/bancada/csv-janitor", { "POST /api/v1/presets": NAME_REJECTED });
    const dialog = await openSaveDialog(user);
    const name = within(dialog).getByLabelText("Nome do preset");
    await user.type(name, "Clientes");
    await user.click(within(dialog).getByRole("button", { name: "Salvar preset" }));

    await waitFor(() => {
      expect(name).toHaveFocus();
    });
    expect(name).toHaveAttribute("aria-invalid", "true");
    expect(name).toHaveAccessibleDescription(
      /Erro: Tire do nome os caracteres invisíveis ou de controle\./,
    );
    expect(within(dialog).queryByText(/Confira os erros indicados/)).toBeNull();
    expect(within(dialog).queryByText(/formato invisível/)).toBeNull();
  });

  it("422 só em outro campo mantém o aviso geral e o nome sem erro", async () => {
    const { user } = setup("/bancada/csv-janitor", {
      "POST /api/v1/presets": () =>
        problemResponse(422, "VALIDATION_FAILED", {
          errors: [
            {
              field: "body.recipe_version",
              code: "value_error",
              message: "versão de receita desconhecida",
            },
          ],
        }),
    });
    const dialog = await openSaveDialog(user);
    await user.type(within(dialog).getByLabelText("Nome do preset"), "Clientes");
    await user.click(within(dialog).getByRole("button", { name: "Salvar preset" }));

    expect(await within(dialog).findByText(/Confira os erros indicados/)).toBeVisible();
    expect(within(dialog).getByLabelText("Nome do preset")).not.toHaveAttribute(
      "aria-invalid",
      "true",
    );
  });

  it("depois do nome recusado, corrigir e salvar fecha o diálogo sem erro velho", async () => {
    let calls = 0;
    const { user } = setup("/bancada/csv-janitor", {
      "POST /api/v1/presets": () =>
        ++calls === 1 ? NAME_REJECTED() : jsonResponse(presetBody(), 201),
    });
    const dialog = await openSaveDialog(user);
    const name = within(dialog).getByLabelText("Nome do preset");
    await user.type(name, "Clientes do mês");
    await user.click(within(dialog).getByRole("button", { name: "Salvar preset" }));
    await waitFor(() => {
      expect(name).toHaveAttribute("aria-invalid", "true");
    });

    await user.click(within(dialog).getByRole("button", { name: "Salvar preset" }));

    await waitFor(() => {
      expect(screen.queryByRole("dialog")).toBeNull();
    });
    expect(calls).toBe(2);
    expect(await screen.findByText("Preset Clientes do mês salvo.")).toBeInTheDocument();
    const reopened = await openSaveDialog(user);
    expect(within(reopened).getByLabelText("Nome do preset")).not.toHaveAttribute(
      "aria-invalid",
      "true",
    );
    expect(within(reopened).queryByText(/Tire do nome/)).toBeNull();
  });

  it("fechar depois do nome recusado e reabrir mostra o campo sem erro", async () => {
    const { user } = setup("/bancada/csv-janitor", { "POST /api/v1/presets": NAME_REJECTED });
    const dialog = await openSaveDialog(user);
    const name = within(dialog).getByLabelText("Nome do preset");
    await user.type(name, "Clientes");
    await user.click(within(dialog).getByRole("button", { name: "Salvar preset" }));
    await waitFor(() => {
      expect(name).toHaveAttribute("aria-invalid", "true");
    });

    await user.click(within(dialog).getByRole("button", { name: "Cancelar" }));
    const reopened = await openSaveDialog(user);

    expect(within(reopened).getByLabelText("Nome do preset")).not.toHaveAttribute(
      "aria-invalid",
      "true",
    );
    expect(within(reopened).queryByText(/Tire do nome/)).toBeNull();
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

    expect(await screen.findByText("Parâmetros do preset Clientes do mês.")).toBeVisible();
    expect(screen.getByLabelText("Delimitador dos arquivos de saída")).toHaveValue(";");
    expect(screen.getByLabelText("Colunas sem espaços nas pontas")).toHaveValue("nome");
  });

  it("anuncia a origem numa região viva que já existia antes de o preset chegar", async () => {
    let release = (): void => undefined;
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    setup(`/bancada/csv-janitor?preset=${PRESET_ID}`, {
      [`GET /api/v1/presets/${PRESET_ID}`]: async () => {
        await held;
        return jsonResponse(presetBody());
      },
    });
    await screen.findByText("Carregando o preset.");
    const regionsBefore = screen.getAllByRole("status");

    release();

    // O leitor de tela só anuncia a mudança numa região que já conhecia: o texto entra num nó
    // que existia (vazio) desde o "Carregando", e não num nó novo que já nasce com ele.
    await waitFor(() => {
      expect(statusWith("Parâmetros do preset Clientes do mês.")).toHaveLength(1);
    });
    expect(regionsBefore).toContain(statusWith("Parâmetros do preset Clientes do mês.")[0]);
  });

  it("o aviso de origem entra na região vazia só depois do atraso do anúncio", async () => {
    let release = (): void => undefined;
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    setup(`/bancada/csv-janitor?preset=${PRESET_ID}`, {
      [`GET /api/v1/presets/${PRESET_ID}`]: async () => {
        await held;
        return jsonResponse(presetBody());
      },
    });
    await screen.findByText("Carregando o preset.");
    installAnnounceClock();
    const notice = "Parâmetros do preset Clientes do mês.";

    release();
    await untilFakeClock(() => {
      expect(screen.getByText(notice, { selector: "p" })).toBeVisible();
    });

    // Alguns leitores perdem o texto que chega logo depois de a região montar.
    expect(statusWith(notice)).toHaveLength(0);
    act(() => {
      vi.advanceTimersByTime(ANNOUNCE_DELAY_MS - 1);
    });
    expect(statusWith(notice)).toHaveLength(0);
    act(() => {
      vi.advanceTimersByTime(1);
    });
    expect(statusWith(notice)).toHaveLength(1);
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

  it("preset do PDF Press com páginas escolhidas abre sem as páginas e pede para escolhê-las de novo", async () => {
    setup(`/bancada/pdf-press?preset=${PRESET_ID}`, {
      [`GET /api/v1/presets/${PRESET_ID}`]: () =>
        jsonResponse(
          presetBody({
            recipe_id: "pdf-press",
            parameters: {
              operation: "select",
              select: [{ upload_id: "0b9f6c1e-3a2d-4e5f-8a7b-6c5d4e3f2a1b", page_number: 2 }],
            },
          }),
        ),
    });

    const notice = await screen.findByText(/Parâmetros do preset Clientes do mês\./);
    expect(notice).toHaveTextContent("Parâmetros do preset Clientes do mês.");
    expect(notice).toHaveTextContent(
      "As páginas escolhidas dependem dos arquivos enviados; escolha-as de novo.",
    );
    expect(screen.queryByText(/Este preset é de outra receita/)).toBeNull();
    expect(screen.getByRole("combobox", { name: "O que fazer com os PDFs" })).toHaveValue("select");
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

const PRESET_ROUTE = `/bancada/csv-janitor?preset=${PRESET_ID}`;
const PUT_PRESET = `PUT /api/v1/presets/${PRESET_ID}`;

/** PUT /presets/{id} que devolve o preset com a revisão seguinte à do `If-Match` recebido. */
function nextRevision(request: Request): Response {
  const revision = Number(request.headers.get("If-Match")?.replaceAll('"', ""));
  return jsonResponse(presetBody({ revision: revision + 1 }));
}

async function openUpdateDialog(user: ReturnType<typeof userEvent.setup>): Promise<HTMLElement> {
  await user.click(await screen.findByRole("button", { name: "Atualizar preset Clientes do mês" }));
  return screen.getByRole("dialog", { name: "Atualizar o preset Clientes do mês?" });
}

async function confirmUpdate(user: ReturnType<typeof userEvent.setup>): Promise<void> {
  const dialog = await openUpdateDialog(user);
  await user.click(within(dialog).getByRole("button", { name: "Atualizar preset" }));
}

describe("Bancada: Atualizar preset", () => {
  it("Bancada aberta pelo preset atualiza os parâmetros com o If-Match da revisão", async () => {
    const { api, user } = setup(PRESET_ROUTE, {
      [`GET /api/v1/presets/${PRESET_ID}`]: () => jsonResponse(presetBody({ revision: 4 })),
      [PUT_PRESET]: nextRevision,
    });
    await user.selectOptions(
      await screen.findByRole("combobox", { name: "Delimitador dos arquivos de saída" }),
      ",",
    );

    const dialog = await openUpdateDialog(user);
    expect(
      within(dialog).getByText(
        "Os parâmetros do formulário substituem os do preset. As execuções feitas com ele não mudam.",
      ),
    ).toBeVisible();
    await user.click(within(dialog).getByRole("button", { name: "Atualizar preset" }));

    expect(await screen.findByText("Preset Clientes do mês atualizado.")).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(sent(api.requests, "POST /api/v1/runs")).toEqual([]);
    const [request] = sent(api.requests, PUT_PRESET);
    expect(request?.headers.get("If-Match")).toBe('"4"');
    expect(request?.headers.get("X-CSRF-Token")).toBe(CSRF);
    expect(await request?.json()).toMatchObject({
      name: "Clientes do mês",
      recipe_id: "csv-janitor",
      recipe_version: 1,
      parameters: expect.objectContaining({
        output_delimiter: ",",
        trim_columns: ["nome"],
      }) as unknown,
    });
  });

  it("aberto, o diálogo de atualizar não tem violações do axe", async () => {
    const { user } = setup(PRESET_ROUTE);

    await openUpdateDialog(user);

    expect(await axeViolations(document.body)).toEqual([]);
  });

  it("segunda atualização usa a revisão devolvida pela primeira", async () => {
    const { api, user } = setup(PRESET_ROUTE, {
      [`GET /api/v1/presets/${PRESET_ID}`]: () => jsonResponse(presetBody({ revision: 4 })),
      [PUT_PRESET]: nextRevision,
    });

    await confirmUpdate(user);
    await screen.findByText("Preset Clientes do mês atualizado.");
    await confirmUpdate(user);

    await waitFor(() => {
      expect(sent(api.requests, PUT_PRESET)).toHaveLength(2);
    });
    expect(
      sent(api.requests, PUT_PRESET).map((request) => request.headers.get("If-Match")),
    ).toEqual(['"4"', '"5"']);
  });

  it("atualizar o preset não relê a origem nem perde as entradas", async () => {
    const { api, user } = setup(PRESET_ROUTE, {
      "POST /api/v1/uploads": uploadIntentsHandler(),
      [PUT_PRESET]: nextRevision,
    });
    await user.upload(await screen.findByLabelText("Escolher arquivos"), [
      new File(["a,b\n1,2\n"], "clientes.csv", { type: "text/csv" }),
    ]);
    expect(await screen.findByText("Enviado")).toBeVisible();

    await confirmUpdate(user);
    await screen.findByText("Preset Clientes do mês atualizado.");

    // Reler `["presets", id]` remontaria o formulário da Bancada e perderia as entradas.
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 50));
    });
    expect(sent(api.requests, `GET /api/v1/presets/${PRESET_ID}`)).toHaveLength(1);
    expect(screen.getByText("clientes.csv")).toBeVisible();
    expect(screen.getByText("Enviado")).toBeVisible();
  });

  it("reaberto pela Oficina depois de atualizar, mostra os parâmetros novos e manda a revisão nova", async () => {
    let stored = presetBody({ revision: 4 });
    const { api, user } = setup(PRESET_ROUTE, {
      "GET /api/v1/runs": () => jsonResponse({ data: [], next_cursor: null }),
      "GET /api/v1/presets": () => jsonResponse({ data: [stored] }),
      [`GET /api/v1/presets/${PRESET_ID}`]: () => jsonResponse(stored),
      [PUT_PRESET]: () => {
        stored = presetBody({
          revision: stored.revision + 1,
          parameters: { ...stored.parameters, output_delimiter: "," },
        });
        return jsonResponse(stored);
      },
    });
    await user.selectOptions(
      await screen.findByRole("combobox", { name: "Delimitador dos arquivos de saída" }),
      ",",
    );
    await confirmUpdate(user);
    await screen.findByText("Preset Clientes do mês atualizado.");

    await user.click(screen.getByRole("link", { name: "Oficina" }));
    await user.click(
      await screen.findByRole("link", { name: "Abrir na Bancada o preset Clientes do mês" }),
    );

    expect(
      await screen.findByRole("combobox", { name: "Delimitador dos arquivos de saída" }),
    ).toHaveValue(",");
    await confirmUpdate(user);
    await waitFor(() => {
      expect(sent(api.requests, PUT_PRESET)).toHaveLength(2);
    });
    expect(
      sent(api.requests, PUT_PRESET).map((request) => request.headers.get("If-Match")),
    ).toEqual(['"4"', '"5"']);
  });

  it("depois de atualizar, o foco volta ao Atualizar preset", async () => {
    const { user } = setup(PRESET_ROUTE, { [PUT_PRESET]: nextRevision });

    await confirmUpdate(user);
    await screen.findByText("Preset Clientes do mês atualizado.");

    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: "Atualizar preset Clientes do mês" }),
      ).toHaveFocus();
    });
  });

  it("Cancelar devolve o foco ao Atualizar preset sem chamar a API", async () => {
    const { api, user } = setup(PRESET_ROUTE, { [PUT_PRESET]: nextRevision });

    const dialog = await openUpdateDialog(user);
    await user.click(within(dialog).getByRole("button", { name: "Cancelar" }));

    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByRole("button", { name: "Atualizar preset Clientes do mês" })).toHaveFocus();
    expect(sent(api.requests, PUT_PRESET)).toEqual([]);
  });

  it("depois de um 412, Confirmar fica bloqueado até fechar e reabrir o diálogo", async () => {
    const { api, user } = setup(PRESET_ROUTE, {
      [PUT_PRESET]: () => problemResponse(412, "PRECONDITION_FAILED"),
    });

    await confirmUpdate(user);
    const dialog = screen.getByRole("dialog", { name: "Atualizar o preset Clientes do mês?" });
    await within(dialog).findByText(/O preset mudou em outra aba ou dispositivo/);
    const confirm = within(dialog).getByRole("button", { name: "Atualizar preset" });
    expect(confirm).toHaveAttribute("aria-disabled", "true");
    await user.click(confirm);
    expect(sent(api.requests, PUT_PRESET)).toHaveLength(1);

    await user.click(within(dialog).getByRole("button", { name: "Cancelar" }));
    const reopened = await openUpdateDialog(user);
    expect(within(reopened).getByRole("button", { name: "Atualizar preset" })).toHaveAttribute(
      "aria-disabled",
      "false",
    );
  });

  it("revisão vencida (412) avisa no diálogo, que continua aberto", async () => {
    const { user } = setup(PRESET_ROUTE, {
      [PUT_PRESET]: () => problemResponse(412, "PRECONDITION_FAILED"),
    });

    await confirmUpdate(user);

    const dialog = screen.getByRole("dialog", { name: "Atualizar o preset Clientes do mês?" });
    expect(
      await within(dialog).findByText(
        "O preset mudou em outra aba ou dispositivo. Abra-o de novo pela Oficina para ver a versão atual.",
      ),
    ).toBeVisible();
    expect(screen.queryByText("Preset Clientes do mês atualizado.")).toBeNull();
  });

  it("outra falha aparece no diálogo", async () => {
    const { user } = setup(PRESET_ROUTE, {
      [PUT_PRESET]: () => problemResponse(422, "VALIDATION_FAILED"),
    });

    await confirmUpdate(user);

    const dialog = screen.getByRole("dialog", { name: "Atualizar o preset Clientes do mês?" });
    expect(await within(dialog).findByRole("alert")).toBeVisible();
  });

  it.each([
    ["sem origem", "/bancada/csv-janitor", {}],
    [
      "por preset de outra receita",
      PRESET_ROUTE,
      {
        [`GET /api/v1/presets/${PRESET_ID}`]: () =>
          jsonResponse(presetBody({ recipe_id: "image-forge", parameters: {} })),
      },
    ],
    [
      "por preset de outra versão da mesma receita",
      PRESET_ROUTE,
      {
        [`GET /api/v1/presets/${PRESET_ID}`]: () => jsonResponse(presetBody({ recipe_version: 2 })),
      },
    ],
    [
      "por execução",
      `/bancada/csv-janitor?execucao=${RUN_ID}`,
      { [`GET /api/v1/runs/${RUN_ID}`]: () => jsonResponse(runBody()) },
    ],
  ] as const)("Bancada aberta %s não oferece Atualizar preset", async (_case, path, handlers) => {
    setup(path, handlers);

    expect(await screen.findByRole("button", { name: "Salvar preset" })).toBeVisible();
    expect(screen.queryByRole("button", { name: /Atualizar preset/ })).toBeNull();
  });

  it("parâmetros inválidos pedem correção em vez de abrir o diálogo", async () => {
    const { api, user } = setup(`/bancada/image-forge?preset=${PRESET_ID}`, {
      [`GET /api/v1/presets/${PRESET_ID}`]: () =>
        jsonResponse(presetBody({ recipe_id: "image-forge", parameters: {} })),
    });

    const width = await screen.findByLabelText("Largura máxima, em pixels");
    await user.clear(width);
    await user.type(width, "0");
    await user.click(screen.getByRole("button", { name: "Atualizar preset Clientes do mês" }));

    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByText("Corrija os parâmetros antes de salvar o preset.")).toBeVisible();
    expect(sent(api.requests, PUT_PRESET)).toEqual([]);
  });
});
