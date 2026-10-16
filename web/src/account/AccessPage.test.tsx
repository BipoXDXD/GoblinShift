import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { apiKeyBody, KEY_ID, meBody, SECOND_KEY_ID } from "../test/account";
import { appRoutes } from "../test/appRoutes";
import { axeViolations } from "../test/axe";
import { fakeApi, jsonResponse, problemResponse, screenRequests } from "../test/fakeApi";
import { expectHeadingFocused } from "../test/focus";
import { currentUrl, renderRoutes } from "../test/renderRoutes";

type Handler = (request: Request) => Response | Promise<Response> | null;

const CSRF = "csrf-da-sessao";
const CANARY = `CANARY-${crypto.randomUUID()}`;
const NO_CONTENT = (): Response => new Response(null, { status: 204 });

function setup(handlers: Readonly<Record<string, Handler>> = {}, path = "/acesso") {
  const api = fakeApi(
    {
      "GET /api/v1/me": () => jsonResponse(meBody()),
      "GET /api/v1/api-keys": () => jsonResponse({ data: [] }),
      "GET /api/v1/runs": () => jsonResponse({ data: [], next_cursor: null }),
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

async function section(name: string): Promise<HTMLElement> {
  return screen.findByRole("region", { name });
}

describe("Acesso: estados da página", () => {
  it("carregando o /me", async () => {
    setup({ "GET /api/v1/me": () => null });

    await expectHeadingFocused("Acesso");
    expect(screen.getByText("Carregando o seu acesso.")).toBeVisible();
  });

  it("sem sessão, convida a entrar e volta para o Acesso", async () => {
    setup({ "GET /api/v1/me": () => problemResponse(401, "AUTHENTICATION_REQUIRED") });

    const prompt = await section("Entre para continuar");
    expect(within(prompt).getByRole("link", { name: "Entrar com Google" })).toHaveAttribute(
      "href",
      "/auth/login/google?return_to=%2Facesso",
    );
    expect(screen.queryByRole("region", { name: "Chaves de API" })).toBeNull();
  });

  it("falha do /me aparece em texto próprio", async () => {
    setup({ "GET /api/v1/me": () => problemResponse(503, "SERVICE_UNAVAILABLE") });

    expect(await screen.findByRole("alert")).toHaveTextContent(/indisponível/);
  });

  it("com chave de API, mostra o uso e explica que o resto exige a sessão", async () => {
    const { api } = setup({ "GET /api/v1/me": () => jsonResponse(meBody({ via: "api_key" })) });

    expect(await screen.findByText(/Este acesso é por chave de API/)).toBeVisible();
    expect(await section("Uso e cotas")).toBeVisible();
    expect(screen.queryByRole("region", { name: "Sessão" })).toBeNull();
    expect(screen.queryByRole("region", { name: "Chaves de API" })).toBeNull();
    expect(sent(api.requests, "GET /api/v1/api-keys")).toEqual([]);
  });
});

describe("Acesso: sessão", () => {
  it("mostra com que conta entrou", async () => {
    setup();

    expect(within(await section("Sessão")).getByText("Você entrou como Ana.")).toBeVisible();
  });

  it("Sair encerra a sessão com o CSRF e volta à Oficina sem os dados da conta", async () => {
    let signedIn = true;
    const { api, user, router } = setup({
      "GET /api/v1/me": () =>
        signedIn ? jsonResponse(meBody()) : problemResponse(401, "AUTHENTICATION_REQUIRED"),
      "POST /auth/logout": () => {
        signedIn = false;
        return NO_CONTENT();
      },
    });

    await user.click(within(await section("Sessão")).getByRole("button", { name: "Sair" }));

    await waitFor(() => {
      expect(currentUrl(router)).toBe("/");
    });
    const [logoutRequest] = sent(api.requests, "POST /auth/logout");
    expect(logoutRequest?.headers.get("X-CSRF-Token")).toBe(CSRF);
    expect(
      await within(screen.getByRole("navigation", { name: "Principal" })).findByRole("link", {
        name: "Entrar",
      }),
    ).toBeVisible();
  });

  it("Sair que falha no CSRF pede para recarregar e fica no Acesso", async () => {
    const { user, router } = setup({
      "POST /auth/logout": () => problemResponse(403, "CSRF_FAILED"),
    });

    await user.click(within(await section("Sessão")).getByRole("button", { name: "Sair" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/Recarregue a página/);
    expect(currentUrl(router)).toBe("/acesso");
  });

  it("Sair de todos pede confirmação e cancelar não faz nada", async () => {
    const { api, user } = setup({ "POST /auth/logout-all": NO_CONTENT });

    await user.click(
      within(await section("Sessão")).getByRole("button", {
        name: "Sair de todos os dispositivos",
      }),
    );
    const dialog = screen.getByRole("dialog", { name: "Sair de todos os dispositivos?" });
    expect(dialog).toHaveAccessibleDescription(/todas as chaves de API da conta são revogadas/);
    await user.click(within(dialog).getByRole("button", { name: "Cancelar" }));

    expect(screen.queryByRole("dialog")).toBeNull();
    expect(sent(api.requests, "POST /auth/logout-all")).toEqual([]);
  });

  it("Sair de todos, confirmado, chama o logout-all com o CSRF e volta à Oficina", async () => {
    const { api, user, router } = setup({ "POST /auth/logout-all": NO_CONTENT });

    await user.click(
      within(await section("Sessão")).getByRole("button", {
        name: "Sair de todos os dispositivos",
      }),
    );
    await user.click(screen.getByRole("button", { name: "Sair de todos e revogar as chaves" }));

    await waitFor(() => {
      expect(currentUrl(router)).toBe("/");
    });
    expect(sent(api.requests, "POST /auth/logout-all")[0]?.headers.get("X-CSRF-Token")).toBe(CSRF);
  });
});

describe("Acesso: uso e cotas", () => {
  it("mostra o consumo em texto, com unidade", async () => {
    setup();

    const usage = await section("Uso e cotas");
    expect(usage).toHaveTextContent("Execuções nas últimas 24 horas3 de 20");
    expect(usage).toHaveTextContent("12,5 MB de 2 GB");
    expect(usage).toHaveTextContent("40 MB de 5 GB");
    expect(within(usage).getAllByRole("meter")).toHaveLength(3);
    expect(usage).toHaveTextContent(/A cota diária começa a liberar em 15\/10\/2026/);
    expect(within(usage).queryByText("No limite")).toBeNull();
  });

  it.each([
    [19, false],
    [20, true],
    [21, true],
  ])("com %i de 20 execuções, 'No limite' aparece: %s", async (runs, atLimit) => {
    setup({
      "GET /api/v1/me": () => jsonResponse(meBody({ usage: { runs_last_24h: runs } })),
    });

    const usage = await section("Uso e cotas");
    expect(within(usage).queryByText("No limite") !== null).toBe(atLimit);
  });

  it("uso zero, sem execução na janela: sem a hora de renovação", async () => {
    setup({
      "GET /api/v1/me": () =>
        jsonResponse(
          meBody({
            usage: {
              runs_last_24h: 0,
              input_bytes_last_24h: 0,
              storage_reserved_bytes: 0,
              daily_quota_resets_at: null,
              executor_ms_last_24h: 0,
            },
          }),
        ),
    });

    const usage = await section("Uso e cotas");
    expect(usage).toHaveTextContent("0 de 20");
    expect(usage).toHaveTextContent("0 bytes de 2 GB");
    expect(usage).not.toHaveTextContent("A cota diária");
  });

  it("execução em andamento vira link para ela", async () => {
    const runId = "0b9c3c1e-7f62-4d1c-9a49-0d6f5d0b8f3a";
    setup({
      "GET /api/v1/me": () => jsonResponse(meBody({ usage: { active_run_id: runId } })),
    });

    expect(
      within(await section("Uso e cotas")).getByRole("link", { name: "Execução em andamento" }),
    ).toHaveAttribute("href", `/execucoes/${runId}`);
  });
});

describe("Acesso: chaves de API", () => {
  it("carregando a lista", async () => {
    setup({ "GET /api/v1/api-keys": () => null });

    expect(within(await section("Chaves de API")).getByText("Carregando as chaves.")).toBeVisible();
  });

  it("sem chaves, o estado vazio tem a ação Nova chave", async () => {
    setup();

    const keys = await section("Chaves de API");
    expect(
      await within(keys).findByRole("heading", { name: "Nenhuma chave de API" }),
    ).toBeVisible();
    expect(within(keys).getByRole("button", { name: "Nova chave" })).toBeVisible();
  });

  it("falha da lista aparece na seção", async () => {
    setup({ "GET /api/v1/api-keys": () => problemResponse(500, "INTERNAL_ERROR") });

    expect(await within(await section("Chaves de API")).findByRole("alert")).toHaveTextContent(
      /Erro interno/,
    );
  });

  it("lista cada chave com estado em texto; só a ativa pode ser revogada", async () => {
    setup({
      "GET /api/v1/api-keys": () =>
        jsonResponse({
          data: [
            apiKeyBody({ public_id: "gsk_ativa" }),
            apiKeyBody({
              id: SECOND_KEY_ID,
              public_id: "gsk_vencida",
              expires_at: "2026-10-01T12:00:00Z",
            }),
            apiKeyBody({
              id: "3c4d5e6f-7a8b-4c9d-8e0f-1a2b3c4d5e6f",
              public_id: "gsk_revogada",
              revoked_at: "2026-10-05T12:00:00Z",
              last_used_at: "2026-10-04T12:00:00Z",
            }),
          ],
        }),
    });

    const list = await screen.findByRole("list", { name: "Chaves de API da conta" });
    const items = within(list).getAllByRole("listitem");
    expect(items.map((item) => item.textContent)).toEqual([
      expect.stringMatching(
        /^gsk_ativaAtivaruns:read, runs:writeCriada em .*Vence em .*Nunca usada/,
      ),
      expect.stringMatching(/^gsk_vencidaExpirada.*Venceu em /),
      expect.stringMatching(/^gsk_revogadaRevogada.*Revogada em .*Último uso em /),
    ]);
    expect(within(list).getAllByRole("button", { name: /^Revogar/ })).toHaveLength(1);
    expect(within(list).getByRole("button", { name: "Revogar a chave gsk_ativa" })).toBeVisible();
  });

  it("a seção de chaves leva ao guia da API", async () => {
    setup();

    const keys = await section("Chaves de API");
    expect(
      within(keys).getByRole("link", { name: "Como usar a chave: guia da API" }),
    ).toHaveAttribute("href", "/acesso/guia-da-api");
  });

  it("revogar pede confirmação; cancelar não chama a API", async () => {
    const { api, user } = setup({
      "GET /api/v1/api-keys": () => jsonResponse({ data: [apiKeyBody()] }),
    });

    await user.click(await screen.findByRole("button", { name: "Revogar a chave gsk_7Hq2" }));
    const dialog = screen.getByRole("dialog", { name: "Revogar a chave gsk_7Hq2?" });
    expect(within(dialog).getByRole("button", { name: "Cancelar" })).toHaveFocus();
    await user.click(within(dialog).getByRole("button", { name: "Cancelar" }));

    expect(sent(api.requests, `DELETE /api/v1/api-keys/${KEY_ID}`)).toEqual([]);
  });

  it("revogar confirmado chama o DELETE com o CSRF, anuncia e relê a lista", async () => {
    let revoked = false;
    const { api, user } = setup({
      "GET /api/v1/api-keys": () =>
        jsonResponse({
          data: [apiKeyBody(revoked ? { revoked_at: "2026-10-14T12:00:00Z" } : {})],
        }),
      [`DELETE /api/v1/api-keys/${KEY_ID}`]: () => {
        revoked = true;
        return NO_CONTENT();
      },
    });

    await user.click(await screen.findByRole("button", { name: "Revogar a chave gsk_7Hq2" }));
    await user.click(screen.getByRole("button", { name: "Revogar chave" }));

    expect(await screen.findByText("Chave gsk_7Hq2 revogada.")).toBeInTheDocument();
    expect(
      sent(api.requests, `DELETE /api/v1/api-keys/${KEY_ID}`)[0]?.headers.get("X-CSRF-Token"),
    ).toBe(CSRF);
    expect(await screen.findByText("Revogada")).toBeVisible();
    expect(screen.queryByRole("button", { name: /^Revogar/ })).toBeNull();
  });

  it("chave que não existe mais (404) mostra 'não encontrada' e relê a lista", async () => {
    let gone = false;
    const { user } = setup({
      "GET /api/v1/api-keys": () => jsonResponse({ data: gone ? [] : [apiKeyBody()] }),
      [`DELETE /api/v1/api-keys/${KEY_ID}`]: () => {
        gone = true;
        return problemResponse(404, "NOT_FOUND");
      },
    });

    await user.click(await screen.findByRole("button", { name: "Revogar a chave gsk_7Hq2" }));
    await user.click(screen.getByRole("button", { name: "Revogar chave" }));

    expect(
      await screen.findByText(
        "Chave não encontrada. Ela pode já ter sido revogada; a lista foi atualizada.",
      ),
    ).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "Nenhuma chave de API" })).toBeVisible();
    expect(screen.queryByRole("alert")).toBeNull();
  });
});

describe("Acesso: chave nova", () => {
  const created = {
    id: KEY_ID,
    public_id: "gsk_nova",
    token: `gsk_nova.${CANARY}`,
    scopes: ["runs:read"],
    expires_at: "2026-11-13T12:00:00Z",
  };

  async function openForm(user: ReturnType<typeof userEvent.setup>): Promise<HTMLElement> {
    await user.click(await screen.findByRole("button", { name: "Nova chave" }));
    return screen.getByRole("form", { name: "Nova chave de API" });
  }

  it("sem escopo e com validade fora da faixa, nada é enviado e o resumo aponta os dois", async () => {
    const { api, user } = setup({ "POST /api/v1/api-keys": () => jsonResponse(created, 201) });
    const form = await openForm(user);

    await user.clear(within(form).getByLabelText("Validade, em dias"));
    await user.type(within(form).getByLabelText("Validade, em dias"), "91");
    await user.click(within(form).getByRole("button", { name: "Criar chave" }));

    const summary = await within(form).findByRole("alert");
    expect(summary).toHaveFocus();
    expect(
      within(summary).getByRole("link", { name: "Escolha pelo menos um escopo." }),
    ).toBeVisible();
    expect(
      within(summary).getByRole("link", { name: "Informe um número inteiro de 1 a 90." }),
    ).toBeVisible();
    expect(sent(api.requests, "POST /api/v1/api-keys")).toEqual([]);
  });

  it("cria com os escopos e a validade, mostra a chave uma vez e copia", async () => {
    const { api, user } = setup({ "POST /api/v1/api-keys": () => jsonResponse(created, 201) });
    const form = await openForm(user);

    await user.click(within(form).getByLabelText("Ver execuções (runs:read)"));
    await user.click(within(form).getByRole("button", { name: "Criar chave" }));

    const reveal = await section("Chave criada: gsk_nova");
    expect(within(reveal).getByLabelText("Chave de API")).toHaveValue(created.token);
    expect(within(reveal).getByRole("heading", { name: "Chave criada: gsk_nova" })).toHaveFocus();
    const [post] = sent(api.requests, "POST /api/v1/api-keys");
    expect(await post?.json()).toEqual({ scopes: ["runs:read"], days: 30 });
    expect(post?.headers.get("X-CSRF-Token")).toBe(CSRF);

    await user.click(within(reveal).getByRole("button", { name: "Copiar chave" }));
    expect(await navigator.clipboard.readText()).toBe(created.token);
    expect(within(reveal).getByRole("status")).toHaveTextContent("Chave copiada.");
  });

  it("o segredo não vai para o storage nem para o console e some com Já copiei", async () => {
    const logged: unknown[] = [];
    for (const method of ["log", "info", "warn", "error", "debug"] as const) {
      vi.spyOn(console, method).mockImplementation((...args: unknown[]) => {
        logged.push(args);
      });
    }
    const { user, queryClient } = setup({
      "POST /api/v1/api-keys": () => jsonResponse(created, 201),
    });
    const form = await openForm(user);
    await user.click(within(form).getByLabelText("Ver execuções (runs:read)"));
    await user.click(within(form).getByRole("button", { name: "Criar chave" }));
    const reveal = await section("Chave criada: gsk_nova");
    // O cache do TanStack não guarda a resposta do POST: o segredo vive só no estado da seção.
    await waitFor(() => {
      const cached = queryClient
        .getMutationCache()
        .getAll()
        .map((mutation) => mutation.state.data);
      expect(JSON.stringify(cached)).not.toContain(CANARY);
    });

    expect(JSON.stringify(Object.entries(localStorage))).not.toContain(CANARY);
    expect(JSON.stringify(Object.entries(sessionStorage))).not.toContain(CANARY);
    expect(JSON.stringify(logged)).not.toContain(CANARY);

    await user.click(within(reveal).getByRole("button", { name: "Já copiei" }));
    expect(screen.queryByDisplayValue(created.token)).toBeNull();
  });

  it("o segredo some ao sair do Acesso e voltar", async () => {
    const { user, router } = setup({ "POST /api/v1/api-keys": () => jsonResponse(created, 201) });
    const form = await openForm(user);
    await user.click(within(form).getByLabelText("Ver execuções (runs:read)"));
    await user.click(within(form).getByRole("button", { name: "Criar chave" }));
    await section("Chave criada: gsk_nova");

    await router.navigate("/historico");
    await screen.findByRole("heading", { level: 1, name: "Histórico" });
    await router.navigate("/acesso");
    await section("Chaves de API");

    expect(screen.queryByDisplayValue(created.token)).toBeNull();
    expect(document.body.innerHTML).not.toContain(CANARY);
  });

  it("sem login recente, pede para entrar de novo e voltar ao Acesso", async () => {
    const { user } = setup({
      "POST /api/v1/api-keys": () => problemResponse(401, "REAUTH_REQUIRED"),
    });
    const form = await openForm(user);
    await user.click(within(form).getByLabelText("Ler arquivos (files:read)"));
    await user.click(within(form).getByRole("button", { name: "Criar chave" }));

    const prompt = await section("Entre de novo para confirmar");
    expect(within(prompt).getByRole("link", { name: "Entrar com GitHub" })).toHaveAttribute(
      "href",
      "/auth/login/github?return_to=%2Facesso&reauth=1",
    );
  });

  it("no teto de chaves ativas (409), explica o que fazer", async () => {
    const { user } = setup({
      "POST /api/v1/api-keys": () => problemResponse(409, "STATE_CONFLICT"),
    });
    const form = await openForm(user);
    await user.click(within(form).getByLabelText("Ler arquivos (files:read)"));
    await user.click(within(form).getByRole("button", { name: "Criar chave" }));

    expect(await within(form).findByRole("alert")).toHaveTextContent(
      "A conta já tem o máximo de chaves ativas. Revogue uma antes de criar outra.",
    );
  });

  it("429 com Retry-After diz quanto esperar", async () => {
    const { user } = setup({
      "POST /api/v1/api-keys": () =>
        problemResponse(429, "RATE_LIMITED", { headers: { "Retry-After": "30" } }),
    });
    const form = await openForm(user);
    await user.click(within(form).getByLabelText("Ler arquivos (files:read)"));
    await user.click(within(form).getByRole("button", { name: "Criar chave" }));

    expect(await within(form).findByRole("alert")).toHaveTextContent(
      "Muitos pedidos em pouco tempo. Aguarde um pouco e tente de novo. Tente de novo em 30 s.",
    );
  });

  it("não tem violações do axe com a lista, o formulário e a chave criada", async () => {
    const { container, user } = setup({
      "GET /api/v1/api-keys": () => jsonResponse({ data: [apiKeyBody()] }),
      "POST /api/v1/api-keys": () => jsonResponse(created, 201),
    });
    const form = await openForm(user);
    expect(await axeViolations(container)).toEqual([]);

    await user.click(within(form).getByLabelText("Ver execuções (runs:read)"));
    await user.click(within(form).getByRole("button", { name: "Criar chave" }));
    await section("Chave criada: gsk_nova");
    expect(await axeViolations(container)).toEqual([]);
  });
});

describe("Acesso: excluir conta", () => {
  async function openDialog(user: ReturnType<typeof userEvent.setup>): Promise<HTMLElement> {
    await user.click(
      within(await section("Excluir conta")).getByRole("button", { name: "Excluir conta…" }),
    );
    return screen.getByRole("dialog", { name: "Excluir a conta?" });
  }

  it("sem a frase digitada, a confirmação fica aria-disabled e nada é enviado", async () => {
    const { api, user } = setup({ "DELETE /api/v1/me": () => new Response(null, { status: 202 }) });
    const dialog = await openDialog(user);
    const phrase = within(dialog).getByLabelText("Digite “excluir minha conta”");
    expect(phrase).toHaveFocus();

    const confirm = within(dialog).getByRole("button", { name: "Excluir conta" });
    expect(confirm).toHaveAttribute("aria-disabled", "true");
    await user.type(phrase, "excluir");
    await user.click(confirm);

    expect(confirm).toHaveAttribute("aria-disabled", "true");
    expect(sent(api.requests, "DELETE /api/v1/me")).toEqual([]);
  });

  it("com a frase, exclui (202), encerra a sessão local e mostra a exclusão pedida", async () => {
    const { api, user, router } = setup({
      "DELETE /api/v1/me": () => new Response(null, { status: 202 }),
    });
    const dialog = await openDialog(user);

    await user.type(
      within(dialog).getByLabelText("Digite “excluir minha conta”"),
      " Excluir Minha Conta ",
    );
    await user.click(within(dialog).getByRole("button", { name: "Excluir conta" }));

    await expectHeadingFocused("Exclusão pedida");
    expect(currentUrl(router)).toBe("/conta-excluida");
    expect(sent(api.requests, "DELETE /api/v1/me")[0]?.headers.get("X-CSRF-Token")).toBe(CSRF);
    expect(screen.getByText(/apagados em até 24 horas/)).toBeVisible();
  });

  it("sem login recente, fecha o diálogo e pede para entrar de novo", async () => {
    const { user } = setup({ "DELETE /api/v1/me": () => problemResponse(401, "REAUTH_REQUIRED") });
    const dialog = await openDialog(user);
    await user.type(
      within(dialog).getByLabelText("Digite “excluir minha conta”"),
      "excluir minha conta",
    );
    await user.click(within(dialog).getByRole("button", { name: "Excluir conta" }));

    const prompt = await section("Entre de novo para confirmar");
    expect(within(prompt).getByRole("link", { name: "Entrar com Google" })).toHaveAttribute(
      "href",
      "/auth/login/google?return_to=%2Facesso&reauth=1",
    );
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("outra falha aparece em texto literal", async () => {
    const { user } = setup({
      "DELETE /api/v1/me": () => problemResponse(503, "SERVICE_UNAVAILABLE"),
    });
    const dialog = await openDialog(user);
    await user.type(
      within(dialog).getByLabelText("Digite “excluir minha conta”"),
      "excluir minha conta",
    );
    await user.click(within(dialog).getByRole("button", { name: "Excluir conta" }));

    expect(await within(await section("Excluir conta")).findByRole("alert")).toHaveTextContent(
      "O serviço está indisponível no momento. Tente de novo mais tarde.",
    );
  });

  it("não tem violações do axe com o diálogo aberto", async () => {
    const { container, user } = setup();
    await openDialog(user);

    expect(await axeViolations(container)).toEqual([]);
  });
});
