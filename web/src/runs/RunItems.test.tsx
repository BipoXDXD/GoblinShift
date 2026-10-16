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
});
