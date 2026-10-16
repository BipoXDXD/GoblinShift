import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { appRoutes } from "../test/appRoutes";
import { axeViolations } from "../test/axe";
import { fakeApi, jsonResponse, runBody, RUN_ID } from "../test/fakeApi";
import { renderRoutes } from "../test/renderRoutes";

const UPLOAD = (n: number) => `7d3c2a1e-0000-4000-8000-${String(n).padStart(12, "0")}`;

function item(position: number, overrides: Record<string, unknown> = {}) {
  return {
    position,
    upload_id: UPLOAD(position),
    original_name: null,
    status: "ok",
    error_code: null,
    warnings: [],
    notices: [],
    counts: {},
    duplicate_of: null,
    formula_cells: null,
    ...overrides,
  };
}

function setup(detail: Record<string, unknown>) {
  const api = fakeApi({
    [`GET /api/v1/runs/${RUN_ID}`]: () =>
      jsonResponse(runBody({ status: "succeeded_with_warnings", ...detail })),
    [`GET /api/v1/runs/${RUN_ID}/results`]: () =>
      jsonResponse({ published: false, expires_at: null, artifacts: [] }),
  });
  return renderRoutes({
    routes: appRoutes(api.client),
    path: `/execucoes/${RUN_ID}`,
    client: api.client,
  });
}

async function itemsRegion(): Promise<HTMLElement> {
  return screen.findByRole("region", { name: "Arquivos da execução" });
}

const summary = { inputs: 4, ok: 2, rejected: 1, duplicates: 1, outputs: 2 };

describe("Execução: detalhe por arquivo", () => {
  it("resume a contagem e lista cada entrada na ordem, com o estado em texto", async () => {
    setup({
      summary,
      items: [
        item(0, {
          counts: { records_read: 1200, filtered_out: 3, duplicates_removed: 0, written: 1197 },
        }),
        item(1, { status: "rejected", error_code: "CSV_STRUCTURE_INVALID" }),
        item(2, { status: "duplicate", duplicate_of: 0 }),
        item(3, { warnings: ["CSV_FORMULA_CELLS"] }),
      ],
    });

    const region = await itemsRegion();
    expect(region).toHaveTextContent(
      "4 arquivos: 2 processados, 1 rejeitado, 1 repetido. 2 arquivos gerados.",
    );
    // Só as entradas: os avisos de cada uma são uma lista dentro do item.
    const list = within(region).getByRole("list", { name: "Arquivos, na ordem de entrada" });
    const entries = [...list.querySelectorAll(":scope > li")];
    expect(entries).toHaveLength(4);
    expect(entries[0]).toHaveTextContent("Arquivo 1");
    expect(entries[0]).toHaveTextContent("Processado");
    expect(entries[0]).toHaveTextContent("1.200 registros lidos");
    expect(entries[0]).toHaveTextContent("3 filtrados");
    expect(entries[0]).toHaveTextContent("1.197 gravados");
    expect(entries[1]).toHaveTextContent("Arquivo 2");
    expect(entries[1]).toHaveTextContent("Rejeitado");
    expect(entries[1]).toHaveTextContent("A estrutura do CSV é inválida");
    expect(entries[2]).toHaveTextContent("Repetido");
    expect(entries[2]).toHaveTextContent("Igual ao arquivo 1");
    expect(entries[3]).toHaveTextContent("Processado com aviso");
    expect(entries[3]).toHaveTextContent("Há células que parecem fórmulas de planilha");
    expect(await axeViolations(region)).toEqual([]);
  });

  it.each([
    [1, "1 registro lido · 1 filtrado · 1 duplicata removida · 1 gravado · 1 linha vazia ignorada"],
    [
      0,
      "0 registros lidos · 0 filtrados · 0 duplicatas removidas · 0 gravados · 0 linhas vazias ignoradas",
    ],
    [
      2,
      "2 registros lidos · 2 filtrados · 2 duplicatas removidas · 2 gravados · 2 linhas vazias ignoradas",
    ],
  ])("contagens por arquivo iguais a %i concordam em número, como o resumo", async (n, text) => {
    const counts = {
      records_read: n,
      filtered_out: n,
      duplicates_removed: n,
      written: n,
      blank_lines_ignored: n,
    };
    setup({ summary, items: [item(0, { counts })] });

    const entry = within(await itemsRegion()).getByRole("listitem");
    expect(entry).toHaveTextContent(`Arquivo 1Processado${text}`);
  });

  it("contagem que a interface não conhece fica de fora; as conhecidas continuam", async () => {
    setup({ summary, items: [item(0, { counts: { pages_merged: 7, written: 5 } })] });

    const entry = within(await itemsRegion()).getByRole("listitem");
    expect(entry).toHaveTextContent(/^Arquivo 1Processado5 gravados$/);
  });

  it("contagem que não é inteiro não derruba a página e fica de fora", async () => {
    setup({ summary, items: [item(0, { counts: { records_read: 1.5, written: 5 } })] });

    const entry = within(await itemsRegion()).getByRole("listitem");
    expect(entry).toHaveTextContent(/^Arquivo 1Processado5 gravados$/);
  });

  it("estado, motivo e aviso que a interface não conhece aparecem como desconhecidos", async () => {
    setup({
      summary,
      items: [
        item(0, { status: "quarantined" }),
        item(1, { status: "rejected", error_code: "PDF_ENCRYPTED" }),
        item(2, { warnings: ["PDF_FONTS_MISSING", "CSV_FORMULA_HEADER"] }),
      ],
    });

    const list = within(await itemsRegion()).getByRole("list", {
      name: "Arquivos, na ordem de entrada",
    });
    const entries = [...list.querySelectorAll(":scope > li")];
    expect(entries[0]).toHaveTextContent("Estado desconhecido");
    expect(entries[1]).toHaveTextContent("Rejeitado");
    expect(entries[1]).toHaveTextContent("Motivo desconhecido (PDF_ENCRYPTED).");
    expect(entries[2]).toHaveTextContent("Processado com aviso");
    expect(entries[2]).toHaveTextContent("Aviso desconhecido (PDF_FONTS_MISSING).");
    expect(entries[2]).toHaveTextContent("O cabeçalho tem nomes que parecem fórmulas");
  });

  it("sem itens (antes da inspeção), a seção não aparece", async () => {
    setup({ status: "queued", summary: null, items: [] });

    expect(await screen.findByText("Na fila")).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Arquivos da execução" })).toBeNull();
  });

  it("entradas ainda não avaliadas aparecem como aguardando, sem resumo", async () => {
    setup({ status: "running", summary: null, items: [item(0, { status: "pending" })] });

    const region = await itemsRegion();
    expect(within(region).getByRole("listitem")).toHaveTextContent("Aguardando");
    expect(region).not.toHaveTextContent("processados");
  });

  it("mostra o nome do arquivo enviado quando a API ainda o guarda", async () => {
    setup({ summary, items: [item(0, { original_name: "clientes.csv" }), item(1)] });

    const entries = within(await itemsRegion()).getAllByRole("listitem");
    expect(entries[0]).toHaveTextContent(/^Arquivo 1clientes\.csvProcessado$/);
    expect(entries[1]).toHaveTextContent(/^Arquivo 2Processado$/);
  });

  it("aviso de PDF sem página escolhida e nota de imagem convertida", async () => {
    setup({
      summary,
      items: [
        item(0, { warnings: ["INPUT_NOT_SELECTED"] }),
        item(1, { notices: ["IMAGE_P3_CONVERTED", "PDF_LINEARIZED"] }),
      ],
    });

    const list = within(await itemsRegion()).getByRole("list", {
      name: "Arquivos, na ordem de entrada",
    });
    const entries = [...list.querySelectorAll(":scope > li")];
    expect(entries[0]).toHaveTextContent("Nenhuma página deste PDF foi escolhida");
    expect(entries[1]).toHaveTextContent("Processado");
    expect(entries[1]).toHaveTextContent(
      "A imagem estava em Display P3 e foi convertida para sRGB.",
    );
    expect(entries[1]).toHaveTextContent("Nota desconhecida (PDF_LINEARIZED).");
  });

  describe("aviso de fórmulas com a contagem por coluna", () => {
    const NOTE = "Valores que começam com “-”, como números negativos, também entram nesta conta";

    function formulaItem(formulaCells: unknown) {
      return item(0, { warnings: ["CSV_FORMULA_CELLS"], formula_cells: formulaCells });
    }

    async function columnsList(): Promise<HTMLElement> {
      return within(await itemsRegion()).findByRole("list", {
        name: "Células que parecem fórmulas, por coluna",
      });
    }

    it("lista as colunas com singular e plural, as que sobraram e a nota sobre o “-”", async () => {
      setup({
        summary,
        items: [
          formulaItem({
            columns: [
              { name: "valor", cells: 1200 },
              { name: "conta", cells: 1 },
            ],
            more_columns: 2,
          }),
        ],
      });

      const columns = within(await columnsList()).getAllByRole("listitem");
      expect(columns.map((column) => column.textContent)).toEqual([
        "valor: 1.200 células",
        "conta: 1 célula",
      ]);
      const region = await itemsRegion();
      expect(region).toHaveTextContent("e mais 2 colunas.");
      expect(region).toHaveTextContent(NOTE);
      expect(await axeViolations(region)).toEqual([]);
    });

    it("uma coluna a mais fica no singular e nenhuma não aparece", async () => {
      setup({
        summary,
        items: [
          formulaItem({ columns: [{ name: "a", cells: 2 }], more_columns: 1 }),
          item(1, {
            warnings: ["CSV_FORMULA_CELLS"],
            formula_cells: { columns: [{ name: "b", cells: 2 }], more_columns: 0 },
          }),
        ],
      });

      const list = within(await itemsRegion()).getByRole("list", {
        name: "Arquivos, na ordem de entrada",
      });
      const entries = [...list.querySelectorAll(":scope > li")];
      expect(entries[0]).toHaveTextContent("e mais 1 coluna.");
      expect(entries[1]).not.toHaveTextContent("e mais");
    });

    it("nome de coluna é texto, nunca HTML", async () => {
      setup({
        summary,
        items: [
          formulaItem({
            columns: [{ name: "<img src=x onerror=alert(1)><b>c</b>", cells: 3 }],
            more_columns: 0,
          }),
        ],
      });

      const list = await columnsList();
      expect(list).toHaveTextContent("<img src=x onerror=alert(1)><b>c</b>: 3 células");
      expect(list.querySelector("img, b")).toBeNull();
    });

    it("nome no teto de 40 caracteres fica inteiro e um acima é cortado com reticências", async () => {
      const atLimit = "x".repeat(40);
      const above = "y".repeat(41);
      setup({
        summary,
        items: [
          formulaItem({
            columns: [
              { name: atLimit, cells: 2 },
              { name: above, cells: 1 },
            ],
            more_columns: 0,
          }),
        ],
      });

      const columns = within(await columnsList()).getAllByRole("listitem");
      expect(columns[0]).toHaveTextContent(`${atLimit}: 2 células`);
      expect(columns[1]).toHaveTextContent(`${"y".repeat(39)}…: 1 célula`);
    });

    it("o corte não parte um caractere fora do plano básico ao meio", async () => {
      setup({
        summary,
        items: [formulaItem({ columns: [{ name: "😀".repeat(41), cells: 1 }], more_columns: 0 })],
      });

      const [column] = within(await columnsList()).getAllByRole("listitem");
      expect(column).toHaveTextContent(`${"😀".repeat(39)}…: 1 célula`);
    });

    it("sem a contagem (execução antiga ou fora da forma), mostra o aviso e a nota", async () => {
      setup({
        summary,
        items: [formulaItem(null), formulaItem({ columns: "valor", more_columns: 0 })],
      });

      const region = await itemsRegion();
      const entries = within(region).getAllByText(/Há células que parecem fórmulas/);
      expect(entries).toHaveLength(2);
      expect(within(region).getAllByText(new RegExp(NOTE))).toHaveLength(2);
      expect(
        within(region).queryByRole("list", { name: "Células que parecem fórmulas, por coluna" }),
      ).toBeNull();
    });
  });
});
