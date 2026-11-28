import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { appRoutes, preloadRoutes } from "../test/appRoutes";
import { axeViolations } from "../test/axe";
import { fakeApi, jsonResponse, runBody, RUN_ID } from "../test/fakeApi";
import { renderRoutes } from "../test/renderRoutes";

await preloadRoutes("/execucoes/id");

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
    comparison: null,
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
    expect(entries[1]).toHaveTextContent("Salve o CSV de novo em UTF-8 e envie outra vez.");
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

  it.each([1.5, -1])("omite a contagem inválida %s e preserva a válida", async (recordsRead) => {
    setup({ summary, items: [item(0, { counts: { records_read: recordsRead, written: 5 } })] });

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

    // A posição é única na execução (chave primária com o run_id): cada item tem a sua.
    function formulaItem(formulaCells: unknown, position = 0) {
      return item(position, { warnings: ["CSV_FORMULA_CELLS"], formula_cells: formulaCells });
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

    it("coluna de cabeçalho vazio aparece com rótulo próprio e a contagem", async () => {
      setup({
        summary,
        items: [
          formulaItem({
            columns: [
              { name: null, cells: 2 },
              { name: "(sem nome)", cells: 1 },
            ],
            more_columns: 0,
          }),
        ],
      });

      const columns = within(await columnsList()).getAllByRole("listitem");
      expect(columns.map((column) => column.textContent)).toEqual([
        "coluna sem nome: 2 células",
        "(sem nome): 1 célula",
      ]);
      // O rótulo é da interface, não do arquivo: em itálico, distinto de um nome real.
      expect(columns[0]?.querySelector("em")).toHaveTextContent("coluna sem nome");
      expect(columns[1]?.querySelector("em")).toBeNull();
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
        items: [formulaItem(null), formulaItem({ columns: "valor", more_columns: 0 }, 1)],
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

  describe("comparação antes e depois", () => {
    const GROUP = { name: "Antes e depois" } as const;

    /** Cada par da lista: o rótulo e o valor, com o espaço normalizado. */
    function definitions(group: HTMLElement): [string, string][] {
      return [...group.querySelectorAll("dt")].map((dt) => [
        normalized(dt.textContent),
        normalized(dt.nextElementSibling?.textContent ?? ""),
      ]);
    }

    function normalized(text: string): string {
      return text.replace(/\s+/g, " ").trim();
    }

    async function comparisonOf(comparison: Record<string, unknown>): Promise<HTMLElement> {
      setup({ summary, items: [item(0, { comparison })] });
      return within(await itemsRegion()).getByRole("group", GROUP);
    }

    const image = {
      kind: "image",
      width_before: 2400,
      height_before: 1200,
      width_after: 1600,
      height_after: 800,
      format_before: "mpo",
      format_after: "jpeg",
      bytes_before: 5_242_880,
      bytes_after: 838_861,
    };

    it("imagem: dimensões, formato e tamanho, com o 'para' para o leitor de tela", async () => {
      const group = await comparisonOf(image);

      expect(definitions(group)).toEqual([
        ["Dimensões", "2.400 × 1.200 → para 1.600 × 800"],
        ["Formato", "MPO → para JPEG"],
        ["Tamanho", "5 MiB → para 819,2 KiB"],
      ]);
    });

    it("imagem sem o tamanho da saída não mostra a linha do tamanho", async () => {
      const group = await comparisonOf({ ...image, format_before: "png", bytes_after: null });

      expect(definitions(group)).toEqual([
        ["Dimensões", "2.400 × 1.200 → para 1.600 × 800"],
        ["Formato", "PNG → para JPEG"],
      ]);
    });

    it("CSV: registros antes e depois, e a linha de contagens só com o que explica a diferença", async () => {
      const counts = { records_read: 120, filtered_out: 3, duplicates_removed: 19, written: 98 };
      setup({
        summary,
        items: [
          item(0, {
            counts,
            comparison: { kind: "table", records_before: 120, records_after: 98 },
          }),
        ],
      });

      const entry = within(await itemsRegion()).getByRole("listitem");
      expect(definitions(within(entry).getByRole("group", GROUP))).toEqual([
        ["Registros", "120 → para 98"],
      ]);
      expect(within(entry).getByText("3 filtrados · 19 duplicatas removidas")).toBeVisible();
      expect(entry).not.toHaveTextContent("registros lidos");
      expect(entry).not.toHaveTextContent("gravados");
    });

    it("PDF: páginas antes e depois", async () => {
      const group = await comparisonOf({ kind: "pdf", pages_before: 12, pages_after: 5 });

      expect(definitions(group)).toEqual([["Páginas", "12 → para 5"]]);
    });

    it("PDF comprimido mostra a linha de tamanho", async () => {
      const group = await comparisonOf({
        kind: "pdf",
        pages_before: 3,
        pages_after: 3,
        bytes_before: 5_242_880,
        bytes_after: 838_861,
      });

      expect(definitions(group)).toEqual([
        ["Páginas", "3 → para 3"],
        ["Tamanho: sem compressão → com compressão", "5 MiB → para 819,2 KiB"],
      ]);
    });

    it("PDF sem compressão não mostra a linha de tamanho", async () => {
      const group = await comparisonOf({
        kind: "pdf",
        pages_before: 3,
        pages_after: 1,
        bytes_before: null,
        bytes_after: null,
      });

      expect(definitions(group)).toEqual([["Páginas", "3 → para 1"]]);
    });

    it("vídeo com miniatura e áudio: o vídeo contra a miniatura, depois o áudio", async () => {
      const group = await comparisonOf({
        kind: "media",
        width_before: 1920,
        height_before: 1080,
        width_after: 320,
        height_after: 180,
        audio_before: "pcm_s24le",
        audio_after: "flac",
      });

      expect(definitions(group)).toEqual([
        ["Dimensões", "1.920 × 1.080 → para 320 × 180"],
        ["Áudio", "PCM 24 bits → para FLAC"],
      ]);
    });

    it("vídeo só com áudio mostra só a linha do áudio", async () => {
      const group = await comparisonOf({
        kind: "media",
        width_before: 1920,
        height_before: 1080,
        width_after: null,
        height_after: null,
        audio_before: "aac",
        audio_after: "wav",
      });

      expect(definitions(group)).toEqual([["Áudio", "AAC → para WAV"]]);
    });

    it("mídia sem miniatura e sem áudio não mostra um bloco vazio", async () => {
      setup({
        summary,
        items: [
          item(0, {
            comparison: {
              kind: "media",
              width_before: 1920,
              height_before: 1080,
              width_after: null,
              height_after: null,
              audio_before: null,
              audio_after: null,
            },
          }),
        ],
      });

      const region = await itemsRegion();
      expect(within(region).getByRole("listitem")).toBeInTheDocument();
      expect(within(region).queryByRole("group", GROUP)).toBeNull();
    });

    it("sem comparação, a entrada fica como antes", async () => {
      setup({ summary, items: [item(0, { counts: { written: 5 } })] });

      const region = await itemsRegion();
      expect(within(region).getByRole("listitem")).toHaveTextContent("5 gravados");
      expect(within(region).queryByRole("group", GROUP)).toBeNull();
    });

    it.each([
      ["tipo", { kind: "zip" }],
      ["formato", { ...image, format_after: "webp" }],
    ])(
      "comparação com %s que a interface não conhece não derruba a tela",
      async (_, comparison) => {
        setup({ summary, items: [item(0, { comparison })] });

        const region = await itemsRegion();
        expect(within(region).getByRole("listitem")).toHaveTextContent("Arquivo 1");
        expect(within(region).queryByRole("group", GROUP)).toBeNull();
      },
    );

    it("não tem violações do axe com a comparação", async () => {
      await comparisonOf(image);

      expect(await axeViolations(await itemsRegion())).toEqual([]);
    });
  });
});
