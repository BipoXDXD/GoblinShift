import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import privacy from "../../../docs/legal/aviso-de-privacidade.md?raw";
import terms from "../../../docs/legal/termos-de-uso.md?raw";
import { appRoutes } from "../test/appRoutes";
import { axeViolations } from "../test/axe";
import { fakeApi, problemResponse } from "../test/fakeApi";
import { expectHeadingFocused } from "../test/focus";
import { renderRoutes } from "../test/renderRoutes";

// Material interno da revisão (pendências, ids do levantamento, notas ao revisor, fontes e as
// instruções de convenção) mora em docs/legal/notas-internas.md, que nada importa.
const INTERNAL_MARKERS = [
  /Pendências/i,
  /\bJ-\d/,
  /notas internas/i,
  /Fontes internas/i,
  /Nota para a revisão/i,
  /Divergências entre plano e código/i,
  /não publicar sem revisão jurídica/i,
  /Esta seção sai do texto final/i,
  /Trechos entre/i,
  /Notas marcadas como/i,
];

// Âncoras publicadas (#secao-...): mudar uma delas é decisão consciente, registrada no Histórico de
// versões do texto, porque quebra links já enviados para fora.
const TERMS_ANCHORS = [
  "secao-1-quem-oferece-o-servico",
  "secao-2-aceite-destes-termos",
  "secao-3-o-que-o-servico-faz",
  "secao-4-conta-e-acesso",
  "secao-5-limites-cotas-e-disponibilidade",
  "secao-6-uso-aceitavel",
  "secao-7-abuso-sexual-infantil-e-outros-conteudos-ilegais",
  "secao-8-suspensao-e-encerramento",
  "secao-9-conteudo-e-propriedade",
  "secao-10-ausencia-de-garantias-e-limitacao-de-responsabilidade",
  "secao-11-privacidade",
  "secao-12-mudancas-nestes-termos",
  "secao-13-lei-aplicavel-e-foro",
  "secao-historico-de-versoes",
];
const PRIVACY_ANCHORS = [
  "secao-1-controlador-e-contato",
  "secao-2-resumo",
  "secao-3-que-dados-tratamos",
  "secao-4-para-que-usamos-e-com-que-base-legal",
  "secao-5-por-quanto-tempo-guardamos",
  "secao-6-com-quem-compartilhamos",
  "secao-7-transferencia-internacional",
  "secao-8-seus-direitos-e-como-exerce-los",
  "secao-9-cookies",
  "secao-10-seguranca",
  "secao-11-criancas-e-adolescentes",
  "secao-12-mudancas-neste-aviso",
  "secao-historico-de-versoes",
];

function setup(path: string) {
  // Os textos são públicos: abrem também sem sessão.
  const api = fakeApi({ "GET /api/v1/me": () => problemResponse(401, "AUTHENTICATION_REQUIRED") });
  return renderRoutes({ routes: appRoutes(api.client), path, client: api.client });
}

// Nome de cada tabela, na ordem: o título acima dela, mesmo com parágrafos no meio. Mudar um título
// com tabela muda o que o leitor de tela anuncia, então a lista acompanha o texto.
const TERMS_TABLES = ["Histórico de versões"];
const PRIVACY_TABLES = [
  "3.1. Dados da conta",
  "4. Para que usamos e com que base legal",
  "5. Por quanto tempo guardamos",
  "6. Com quem compartilhamos",
  "8. Seus direitos e como exercê-los",
  "9. Cookies",
  "Histórico de versões",
];

describe.each([
  ["/termos", "Termos de Uso do GoblinShift", terms, TERMS_TABLES],
  ["/privacidade", "Aviso de Privacidade do GoblinShift", privacy, PRIVACY_TABLES],
])("%s", (path, title, source, tableNames) => {
  it("o título do arquivo é o h1 da tela, sem aviso de rascunho", async () => {
    setup(path);

    await expectHeadingFocused(title);
    expect(screen.queryByRole("complementary")).not.toBeInTheDocument();
  });

  it("cada seção ## do arquivo vira um h2", async () => {
    setup(path);
    await expectHeadingFocused(title);

    const expected = [...source.matchAll(/^## (.+)$/gm)].map(([, heading]) =>
      (heading ?? "").replaceAll("**", ""),
    );
    const shown = screen
      .getAllByRole("heading", { level: 2 })
      .map((heading) => heading.textContent);
    expect(expected.length).toBeGreaterThan(5);
    expect(shown).toEqual(expected);
  });

  it("o arquivo embutido no build não traz material interno da revisão", () => {
    for (const marker of INTERNAL_MARKERS) {
      expect(source).not.toMatch(marker);
    }
  });

  it("a página não mostra material interno nem marcações a definir", async () => {
    setup(path);
    await expectHeadingFocused(title);

    const shown = screen.getByRole("article").textContent;
    for (const marker of INTERNAL_MARKERS) {
      expect(shown).not.toMatch(marker);
    }
    expect(shown).not.toContain("[[A DEFINIR");
  });

  it("não tem violações do axe", async () => {
    const { container } = setup(path);
    await expectHeadingFocused(title);

    expect(await axeViolations(container)).toEqual([]);
  });

  // WCAG 4.1.2: a caixa rolável recebe foco, e o leitor de tela precisa dizer o que há nela.
  it("cada tabela tem legenda e rola numa região com nome", async () => {
    setup(path);
    await expectHeadingFocused(title);

    const tables = screen.getAllByRole("table");
    // Só as regiões das tabelas: outra região da tela não tem a ver com o B1.
    const regions = screen
      .getAllByRole("region")
      .filter((region) => within(region).queryByRole("table") !== null);
    expect(regions).toHaveLength(tables.length);
    for (const region of regions) {
      expect(region).toHaveAccessibleName(/\S/);
    }
    for (const table of tables) {
      expect(table.querySelector("caption")?.textContent).toMatch(/\S/);
    }
  });

  it("cada tabela leva o nome do título que a precede", async () => {
    setup(path);
    await expectHeadingFocused(title);

    expect(screen.getAllByRole("table")).toHaveLength(tableNames.length);
    for (const name of tableNames) {
      const region = screen.getByRole("region", { name });
      expect(within(region).getByRole("table")).toHaveAccessibleName(name);
    }
  });

  it("o sumário tem um link por h2, na ordem e com o mesmo texto", async () => {
    setup(path);
    await expectHeadingFocused(title);

    const sections = screen
      .getAllByRole("heading", { level: 2 })
      .map((heading) => heading.textContent);
    const outline = screen.getByRole("navigation", { name: "Sumário" });
    const entries = within(outline)
      .getAllByRole("link")
      .map((link) => link.textContent);
    expect(entries).toEqual(sections);
  });

  it("cada link do sumário leva ao h2 da seção", async () => {
    setup(path);
    await expectHeadingFocused(title);

    const outline = screen.getByRole("navigation", { name: "Sumário" });
    for (const link of within(outline).getAllByRole("link")) {
      const href = link.getAttribute("href") ?? "";
      expect(href).toMatch(/^#secao-[a-z0-9-]+$/);
      const target = document.getElementById(href.slice(1));
      expect(target?.tagName).toBe("H2");
      expect(target).toHaveTextContent(link.textContent);
    }
  });
});

describe("link de fora com âncora", () => {
  it("abre com o foco no h2 da seção, não no h1", async () => {
    setup("/privacidade#secao-8-seus-direitos-e-como-exerce-los");

    await waitFor(() => {
      expect(
        screen.getByRole("heading", { level: 2, name: "8. Seus direitos e como exercê-los" }),
      ).toHaveFocus();
    });
  });

  it("o h1 não recebe o foco no caminho: o leitor de tela anuncia só a seção", async () => {
    const focused: string[] = [];
    const record = (event: FocusEvent) => {
      if (event.target instanceof Element) {
        focused.push(event.target.tagName);
      }
    };
    document.addEventListener("focusin", record);
    try {
      setup("/privacidade#secao-8-seus-direitos-e-como-exerce-los");
      await waitFor(() => {
        expect(focused).toContain("H2");
      });
    } finally {
      document.removeEventListener("focusin", record);
    }

    expect(focused).not.toContain("H1");
  });

  it("âncora que não existe mais cai no h1", async () => {
    setup("/termos#secao-que-nao-existe");

    await expectHeadingFocused("Termos de Uso do GoblinShift");
  });
});

describe("âncoras travadas", () => {
  // Link para uma seção sai da página (resposta a titular, e-mail de suporte). Mudar um título ou
  // renumerar as seções quebra esses links em silêncio: este teste obriga a decisão consciente.
  it.each([
    ["/termos", "Termos de Uso do GoblinShift", TERMS_ANCHORS],
    ["/privacidade", "Aviso de Privacidade do GoblinShift", PRIVACY_ANCHORS],
  ])("%s mantém as âncoras publicadas", async (path, title, anchors) => {
    setup(path);
    await expectHeadingFocused(title);

    const shown = screen.getAllByRole("heading", { level: 2 }).map((heading) => heading.id);
    expect(shown).toEqual(anchors);
  });
});

describe("posição do sumário", () => {
  it.each([
    ["/termos", "Termos de Uso do GoblinShift"],
    ["/privacidade", "Aviso de Privacidade do GoblinShift"],
  ])(
    "%s: o sumário vem depois de Versão e Vigência e antes do primeiro h2",
    async (path, title) => {
      setup(path);
      await expectHeadingFocused(title);

      const outline = screen.getByRole("navigation", { name: "Sumário" });
      const validity = screen.getByText("Vigência:");
      const firstSection = screen.getAllByRole("heading", { level: 2 }).at(0) ?? outline;
      expect(validity.compareDocumentPosition(outline)).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
      expect(outline.compareDocumentPosition(firstSection)).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
    },
  );
});

describe("e-mails de contato", () => {
  it.each([
    ["/privacidade", "Aviso de Privacidade do GoblinShift", "privacidade@goblinshift.dev"],
    ["/termos", "Termos de Uso do GoblinShift", "privacidade@goblinshift.dev"],
    ["/termos", "Termos de Uso do GoblinShift", "contato@goblinshift.dev"],
    ["/termos", "Termos de Uso do GoblinShift", "abuso@goblinshift.dev"],
  ])("%s: %s abre o e-mail", async (path, title, address) => {
    setup(path);
    await expectHeadingFocused(title);

    const links = within(screen.getByRole("article")).getAllByRole("link", { name: address });
    expect(links.length).toBeGreaterThan(0);
    for (const link of links) {
      expect(link).toHaveAttribute("href", `mailto:${address}`);
    }
  });
});

describe("links entre os textos", () => {
  it("o link dos Termos para o aviso abre a rota da interface", async () => {
    setup("/termos");
    await expectHeadingFocused("Termos de Uso do GoblinShift");

    const links = within(screen.getByRole("article")).getAllByRole("link", {
      name: "Aviso de Privacidade",
    });
    expect(links.length).toBeGreaterThan(0);
    for (const link of links) {
      expect(link).toHaveAttribute("href", "/privacidade");
    }
  });
});

describe("links para fora da interface", () => {
  it.each([
    "https://goblinshift.dev/.well-known/security.txt",
    "https://github.com/BipoXDXD/GoblinShift/blob/main/LICENSE",
    "https://github.com/BipoXDXD/GoblinShift/blob/main/docs/art/LICENSE-ART.md",
  ])("os Termos levam a %s", async (href) => {
    setup("/termos");
    await expectHeadingFocused("Termos de Uso do GoblinShift");

    const links = within(screen.getByRole("article")).getAllByRole("link", { name: href });
    expect(links.length).toBeGreaterThan(0);
    for (const link of links) {
      expect(link).toHaveAttribute("href", href);
    }
  });
});
