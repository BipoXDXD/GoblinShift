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
    status: "ok",
    error_code: null,
    warnings: [],
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
        item(1, {
          counts: { records_read: 1200, filtered_out: 3, duplicates_removed: 0, written: 1197 },
        }),
        item(2, { status: "rejected", error_code: "CSV_STRUCTURE_INVALID" }),
        item(3, { status: "duplicate", duplicate_of: 1 }),
        item(4, { warnings: ["CSV_FORMULA_CELLS"] }),
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
    expect(entries[1]).toHaveTextContent("Rejeitado");
    expect(entries[1]).toHaveTextContent("A estrutura do CSV é inválida");
    expect(entries[2]).toHaveTextContent("Repetido");
    expect(entries[2]).toHaveTextContent("Igual ao arquivo 1");
    expect(entries[3]).toHaveTextContent("Processado com aviso");
    expect(entries[3]).toHaveTextContent("Há células que parecem fórmulas de planilha");
    expect(await axeViolations(region)).toEqual([]);
  });

  it("contagem que a interface não conhece não aparece", async () => {
    setup({ summary, items: [item(1, { counts: { pages_merged: 7 } })] });

    const region = await itemsRegion();
    expect(region).not.toHaveTextContent("pages_merged");
    expect(region).not.toHaveTextContent("7");
  });

  it("sem itens (antes da inspeção), a seção não aparece", async () => {
    setup({ status: "queued", summary: null, items: [] });

    expect(await screen.findByText("Na fila")).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Arquivos da execução" })).toBeNull();
  });

  it("entradas ainda não avaliadas aparecem como aguardando, sem resumo", async () => {
    setup({ status: "running", summary: null, items: [item(1, { status: "pending" })] });

    const region = await itemsRegion();
    expect(within(region).getByRole("listitem")).toHaveTextContent("Aguardando");
    expect(region).not.toHaveTextContent("processados");
  });
});
