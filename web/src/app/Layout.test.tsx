import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { NavigationType } from "react-router";
import { describe, expect, it } from "vitest";

import { ME_QUERY_KEY } from "../account/api";
import type { SiteNotice } from "../notice/notice";
import { meBody, TERMS_PENDING } from "../test/account";
import { appRoutes, preloadRoutes } from "../test/appRoutes";
import { axeViolations } from "../test/axe";
import { fakeApi, jsonResponse, problemResponse, TEST_CSRF_TOKEN } from "../test/fakeApi";
import { expectHeadingFocused } from "../test/focus";
import { currentUrl, renderRoutes } from "../test/renderRoutes";

await preloadRoutes(
  "/termos",
  "/privacidade",
  "/creditos",
  "/historico",
  "/entrar",
  "/acesso",
  "/termos/aceitar",
);

type MeHandler = () => Response | null;

const REF = "0b8f3c2e-5d4a-4f1b-9c7e-2a6d8e0f1b3c";
const HOME_TITLE = "Arquivos em lote, limpos pelas goblins da oficina.";

function setup(
  me: MeHandler,
  path = "/",
  notice: SiteNotice | null = null,
  csrfToken: string | null = TEST_CSRF_TOKEN,
) {
  const api = layoutApi(me, csrfToken);
  const view = renderRoutes({
    routes: appRoutes(api.client, { notice }),
    path,
    client: api.client,
  });
  return { ...view, api };
}

function layoutApi(me: MeHandler, csrfToken: string | null = TEST_CSRF_TOKEN) {
  return fakeApi(
    {
      "GET /api/v1/me": me,
      "GET /api/v1/runs": () => jsonResponse({ data: [], next_cursor: null }),
    },
    { csrfToken },
  );
}

/** O cabeçalho só aparece depois do chunk da primeira tela (rotas lazy). */
function nav(): Promise<HTMLElement> {
  return screen.findByRole("navigation", { name: "Principal" });
}

describe("Layout: marca no cabeçalho", () => {
  it("o nome em texto é o nome acessível do link da Oficina", async () => {
    setup(() => jsonResponse(meBody()));
    await nav();

    const home = screen.getByRole("link", { name: "GoblinShift" });
    expect(home).toHaveAttribute("href", "/");
    expect(home).not.toHaveAttribute("aria-label");
    expect(within(home).getByText("GoblinShift")).toBeVisible();
  });

  it("o emblema é decorativo, com tamanho fixo (sem CLS) e AVIF antes do WebP", async () => {
    setup(() => jsonResponse(meBody()));
    await nav();

    const home = screen.getByRole("link", { name: "GoblinShift" });
    // alt="" tira a imagem da árvore de acessibilidade: o nome já está em texto ao lado.
    expect(within(home).queryByRole("img")).toBeNull();
    const emblem = home.querySelector("img");
    expect(emblem).toHaveAttribute("alt", "");
    expect(emblem).toHaveAttribute("width", "45");
    expect(emblem).toHaveAttribute("height", "48");
    const types = [...home.querySelectorAll("source")].map((source) => source.type);
    expect(types).toEqual(["image/avif", "image/webp"]);
  });
});

describe("Layout: conta no cabeçalho", () => {
  it("com sessão, o link é Acesso", async () => {
    setup(() => jsonResponse(meBody()));

    expect(await within(await nav()).findByRole("link", { name: "Acesso" })).toHaveAttribute(
      "href",
      "/acesso",
    );
  });

  it("sem sessão, o mesmo lugar vira Entrar, voltando para a tela atual", async () => {
    setup(() => problemResponse(401, "AUTHENTICATION_REQUIRED"), "/historico?estado=failed");

    expect(await within(await nav()).findByRole("link", { name: "Entrar" })).toHaveAttribute(
      "href",
      "/entrar?volta=%2Fhistorico%3Festado%3Dfailed",
    );
    expect(within(await nav()).queryByRole("link", { name: "Acesso" })).toBeNull();
  });

  it("sem o cookie de sessão, mostra Entrar sem chamar o /me", async () => {
    const { api } = setup(() => jsonResponse(meBody()), "/", null, null);

    expect(await within(await nav()).findByRole("link", { name: "Entrar" })).toBeVisible();
    expect(api.requests.map((request) => new URL(request.url).pathname)).not.toContain(
      "/api/v1/me",
    );
  });

  it("enquanto o /me carrega, mostra Acesso (sem pular quando a resposta chega)", async () => {
    setup(() => null);

    expect(await within(await nav()).findByRole("link", { name: "Acesso" })).toBeVisible();
  });

  it("outra falha do /me não esconde a navegação", async () => {
    setup(() => problemResponse(503, "SERVICE_UNAVAILABLE"));

    expect(await within(await nav()).findByRole("link", { name: "Acesso" })).toBeVisible();
    expect(within(await nav()).getByRole("link", { name: "Histórico" })).toBeVisible();
  });
});

describe("Layout: termos pendentes", () => {
  it("leva ao aceite antes de qualquer tela, guardando a volta", async () => {
    const { router } = setup(
      () => jsonResponse(meBody({ terms: TERMS_PENDING })),
      "/historico?estado=failed",
    );

    await waitFor(() => {
      expect(currentUrl(router)).toBe("/termos/aceitar?volta=%2Fhistorico%3Festado%3Dfailed");
    });
    expect(await screen.findByRole("heading", { level: 1, name: "Termos de Uso" })).toBeVisible();
  });

  it("conta que aceitou a versão anterior volta ao aceite quando a versão sobe", async () => {
    const { router } = setup(
      () =>
        jsonResponse(
          meBody({
            terms: {
              current_version: "v2",
              accepted_version: "v1",
              accepted_at: "2026-10-14T12:00:00Z",
              acceptance_required: true,
            },
          }),
        ),
      "/historico",
    );

    await waitFor(() => {
      expect(currentUrl(router)).toBe("/termos/aceitar?volta=%2Fhistorico");
    });
    expect(await screen.findByRole("heading", { level: 1, name: "Termos de Uso" })).toBeVisible();
  });

  it.each(["/termos", "/privacidade", "/acesso", "/entrar"])(
    "%s continua aberta para ler, sair ou excluir a conta",
    async (path) => {
      const { router } = setup(() => jsonResponse(meBody({ terms: TERMS_PENDING })), path);

      expect(await screen.findByRole("heading", { level: 1 })).toBeVisible();
      expect(currentUrl(router)).toBe(path);
    },
  );

  it.each(["/termos/", "/privacidade/", "/creditos/"])(
    "%s, com a barra final, também fica aberta para ler",
    async (path) => {
      const { router, queryClient } = setup(
        () => jsonResponse(meBody({ terms: TERMS_PENDING })),
        path,
      );

      // A tela pinta antes do /me: só com a resposta o aceite pendente poderia redirecionar.
      await waitFor(() => {
        expect(queryClient.getQueryState(ME_QUERY_KEY)?.status).toBe("success");
      });
      expect(await screen.findByRole("heading", { level: 1 })).toBeVisible();
      expect(currentUrl(router)).toBe(path.slice(0, -1));
    },
  );

  it("com os termos aceitos, a tela abre normalmente", async () => {
    const { router } = setup(() => jsonResponse(meBody()), "/historico");

    expect(await screen.findByRole("heading", { level: 1, name: "Histórico" })).toBeVisible();
    expect(currentUrl(router)).toBe("/historico");
  });
});

describe("Layout: login recusado", () => {
  it("?login_error=failed mostra o erro literal, sem o motivo", async () => {
    setup(() => problemResponse(401, "AUTHENTICATION_REQUIRED"), "/?login_error=failed");

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Não foi possível entrar. Tente de novo com a mesma conta ou com o outro provedor.",
    );
  });

  it("mostra o código de referência do fragmento, para citar no recurso", async () => {
    setup(() => problemResponse(401, "AUTHENTICATION_REQUIRED"), `/?login_error=failed#ref=${REF}`);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/^Não foi possível entrar\./);
    expect(within(alert).getByText(`Código do pedido para suporte: ${REF}`)).toBeVisible();
  });

  it.each([
    ["texto injetado", "#ref=Escreva%20para%20golpe%40example.com"],
    ["HTML", "#ref=%3Cb%3EPERIGO%3C%2Fb%3E"],
    ["quase um UUID", `#ref=${REF}0`],
    ["sem código", ""],
  ])("código inválido (%s) não aparece, e o erro continua", async (_name, hash) => {
    setup(() => problemResponse(401, "AUTHENTICATION_REQUIRED"), `/?login_error=failed${hash}`);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(
      "Não foi possível entrar. Tente de novo com a mesma conta ou com o outro provedor.",
    );
    expect(within(alert).queryByText(/Código/)).toBeNull();
    expect(alert).not.toHaveTextContent(/golpe|PERIGO|<b>/);
  });

  it("o foco inicial fica no alerta, e o h1 da Oficina não o rouba ao aparecer", async () => {
    setup(() => problemResponse(401, "AUTHENTICATION_REQUIRED"), `/?login_error=failed#ref=${REF}`);

    const alert = await screen.findByRole("alert");
    // A Oficina é lazy: o h1 chega depois do alerta, e o efeito dele roda depois do findBy.
    await screen.findByRole("heading", { level: 1, name: HOME_TITLE });
    await act(() => Promise.resolve());

    expect(alert).toHaveFocus();
  });

  it("sem o login_error, o foco inicial continua no h1 da Oficina", async () => {
    setup(() => jsonResponse(meBody()), "/");

    await expectHeadingFocused(HOME_TITLE);
  });

  it("código sem o login_error não mostra nada", async () => {
    setup(() => jsonResponse(meBody()), `/#ref=${REF}`);

    expect(await screen.findByRole("heading", { level: 1 })).toBeVisible();
    expect(screen.queryByText(new RegExp(REF))).toBeNull();
  });

  it.each(["/?login_error=outro", "/?login_error=", "/"])(
    "%s não mostra erro de login",
    async (path) => {
      setup(() => jsonResponse(meBody()), path);

      expect(await screen.findByRole("heading", { level: 1 })).toBeVisible();
      expect(screen.queryByText(/Não foi possível entrar/)).toBeNull();
    },
  );

  it("não tem violações do axe com o erro de login e o código", async () => {
    const { container } = setup(
      () => problemResponse(401, "AUTHENTICATION_REQUIRED"),
      `/?login_error=failed#ref=${REF}`,
    );
    await within(await nav()).findByRole("link", { name: "Entrar" });

    expect(await axeViolations(container)).toEqual([]);
  });
});

describe("Layout: aviso global", () => {
  /** Na janela do relógio fixo dos testes (14/10/2026 12:00 UTC). */
  const INCIDENT: SiteNotice = {
    id: "incidente-2026-10",
    kind: "incident",
    title: "Incidente de segurança em andamento",
    body: "Detalhes no aviso de privacidade.",
    link: { href: "/privacidade", label: "Ler o aviso" },
    fromMs: Date.parse("2026-10-14T00:00:00Z"),
    untilMs: null,
  };

  it.each([
    ["com sessão", () => jsonResponse(meBody())],
    ["sem sessão", () => problemResponse(401, "AUTHENTICATION_REQUIRED")],
  ])("%s, aparece entre a barra e o conteúdo", async (_case, me) => {
    setup(me, "/historico", INCIDENT);
    await nav();

    const notice = screen.getByRole("region", { name: "Aviso: incidente de segurança" });
    const header = screen.getByRole("banner");
    const main = screen.getByRole("main");
    expect(header.compareDocumentPosition(notice)).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
    expect(notice.compareDocumentPosition(main)).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
  });

  it("fora da janela do relógio das rotas, não aparece", async () => {
    setup(() => jsonResponse(meBody()), "/historico", {
      ...INCIDENT,
      fromMs: Date.parse("2026-10-14T12:00:00.001Z"),
    });
    await nav();

    expect(screen.queryByRole("region", { name: /^Aviso/ })).toBeNull();
  });

  it("sem aviso no build, não aparece", async () => {
    setup(() => jsonResponse(meBody()), "/historico");
    await nav();

    expect(screen.queryByRole("region", { name: /^Aviso/ })).toBeNull();
  });

  it("não tem violações do axe com o aviso na moldura", async () => {
    const { container } = setup(() => jsonResponse(meBody()), "/historico", INCIDENT);
    await screen.findByRole("heading", { level: 1, name: "Histórico" });

    expect(await axeViolations(container)).toEqual([]);
  });

  it("Fechar aviso leva o foco ao título da tela, não ao corpo da página", async () => {
    setup(() => jsonResponse(meBody()), "/historico", {
      ...INCIDENT,
      id: "termos-2026-11",
      kind: "terms-change",
    });
    // O h1 aparece no DOM antes do efeito de montagem do FocusHeading, que é quem lhe dá o foco.
    // Sem esperar por ele, o efeito pode rodar depois do close.focus() abaixo (CPU disputada),
    // o Enter cairia no título e o aviso não fecharia (#310).
    await expectHeadingFocused("Histórico");
    const title = screen.getByRole("heading", { level: 1, name: "Histórico" });
    const close = screen.getByRole("button", { name: "Fechar aviso" });
    close.focus();
    expect(close).toHaveFocus();

    await userEvent.keyboard("{Enter}");

    expect(screen.queryByRole("region", { name: /^Aviso/ })).toBeNull();
    expect(title).toHaveFocus();
  });
});

describe("Layout: rodapé", () => {
  it("leva aos Termos, à Privacidade e aos Créditos, com alvo de 44 px", async () => {
    setup(() => jsonResponse(meBody()));

    const footer = await screen.findByRole("contentinfo");
    const links = within(footer).getAllByRole("link");
    expect(links.map((link) => [link.textContent, link.getAttribute("href")])).toEqual([
      ["Termos de Uso", "/termos"],
      ["Aviso de Privacidade", "/privacidade"],
      ["Créditos", "/creditos"],
    ]);
    for (const link of links) {
      expect(link).toHaveClass("min-h-11");
    }
  });

  it.each([
    ["/termos", ["page", null, null]],
    ["/termos/", ["page", null, null]],
    ["/privacidade", [null, "page", null]],
    ["/creditos", [null, null, "page"]],
  ])("em %s, só o link da página atual tem aria-current", async (path, expected) => {
    setup(() => jsonResponse(meBody()), path);

    const footer = await screen.findByRole("contentinfo");
    // Com barra final, o rodapé aparece antes do redirect para o endereço sem barra.
    await waitFor(() => {
      const links = within(footer).getAllByRole("link");
      expect(links.map((link) => link.getAttribute("aria-current"))).toEqual(expected);
    });
  });

  it("a página atual se destaca também sem cor: perde o sublinhado, como no cabeçalho", async () => {
    setup(() => jsonResponse(meBody()), "/termos");

    const footer = await screen.findByRole("contentinfo");
    const current = within(footer).getByRole("link", { name: "Termos de Uso" });
    expect(current).toHaveClass("aria-[current=page]:text-fg", "aria-[current=page]:no-underline");
  });

  it.each(["/historico", "/termos/aceitar"])(
    "em %s, nenhum link do rodapé é a página atual",
    async (path) => {
      setup(() => jsonResponse(meBody()), path);

      const footer = await screen.findByRole("contentinfo");
      for (const link of within(footer).getAllByRole("link")) {
        expect(link).not.toHaveAttribute("aria-current");
      }
    },
  );
});

describe("Layout: barra final no endereço", () => {
  it("vai para o endereço sem a barra, mantendo a query e o fragmento", async () => {
    const { router } = setup(() => jsonResponse(meBody()), "/historico/?estado=failed#topo");

    await waitFor(() => {
      expect(currentUrl(router)).toBe("/historico?estado=failed#topo");
    });
  });

  it("troca a entrada do histórico: o Voltar leva à tela anterior, sem prender na barra", async () => {
    const api = layoutApi(() => jsonResponse(meBody()));
    const { router } = renderRoutes({
      routes: appRoutes(api.client, { notice: null }),
      visited: ["/historico"],
      path: "/termos/",
      client: api.client,
    });

    await waitFor(() => {
      expect(currentUrl(router)).toBe("/termos");
    });
    expect(router.state.historyAction).toBe(NavigationType.Replace);

    await act(() => router.navigate(-1));
    expect(currentUrl(router)).toBe("/historico");
  });

  // O navegador entrega a forma codificada como está: vira um caminho deste site, não outro host.
  it.each(["/%2F%2Fevil.com/", "/%5Cevil.com/"])(
    "%s vira o caminho local literal, sem a barra",
    async (path) => {
      const { router } = setup(() => jsonResponse(meBody()), path);

      await waitFor(() => {
        expect(currentUrl(router)).toBe(path.slice(0, -1));
      });
    },
  );

  it.each(["//evil.com/", "/\\evil.com/"])("%s fica no site, sem redirecionar", async (path) => {
    const { router } = setup(() => jsonResponse(meBody()), path);

    expect(await screen.findByRole("heading", { level: 1 })).toBeVisible();
    expect(currentUrl(router)).toBe(path);
  });
});
