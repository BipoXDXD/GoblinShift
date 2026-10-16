import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { appRoutes } from "../test/appRoutes";
import { axeViolations } from "../test/axe";
import { fakeApi, jsonResponse, runBody } from "../test/fakeApi";
import { renderRoutes } from "../test/renderRoutes";

// O relógio das telas nos testes é 14/10/2026 12:00 UTC (test/appRoutes.ts).
const RUN_ID = "0b9c3c1e-7f62-4d1c-9a49-0d6f5d0b8f3a";

function renderHistory(run: Record<string, unknown>) {
  const api = fakeApi({
    "GET /api/v1/runs": () =>
      jsonResponse({ data: [runBody({ id: RUN_ID, ...run })], next_cursor: null }),
  });
  return renderRoutes({ routes: appRoutes(api.client), path: "/historico", client: api.client });
}

async function row(): Promise<HTMLElement> {
  const list = await screen.findByRole("list", { name: "Execuções" });
  const [first] = within(list).getAllByRole("listitem");
  if (first === undefined) throw new Error("sem linha");
  return first;
}

describe("Histórico: disponibilidade dos resultados", () => {
  it.each([
    ["2026-10-15T09:00:00Z", /^Resultados até /],
    ["2026-10-14T11:00:00Z", /^Resultados expirados$/],
    [null, /^Resultados indisponíveis$/],
  ])("concluída com validade %s", async (expiresAt, text) => {
    renderHistory({ status: "succeeded", results_expire_at: expiresAt });

    expect(within(await row()).getByText(text)).toBeVisible();
  });

  it.each(["queued", "running", "failed", "canceled"])(
    "%s não fala de resultados",
    async (status) => {
      renderHistory({ status, results_expire_at: null });

      expect(await row()).not.toHaveTextContent("Resultados");
    },
  );
});

describe("Histórico: reusar a configuração", () => {
  it("leva à Bancada da receita com os parâmetros da execução, sem ser coberto pelo link da linha", async () => {
    const { container } = renderHistory({
      status: "succeeded",
      parameters: { output_delimiter: ";" },
    });

    const reuse = within(await row()).getByRole("link", { name: /^Reusar configuração/ });
    expect(reuse).toHaveAttribute("href", `/bancada/csv-janitor?execucao=${RUN_ID}`);
    expect(reuse).toHaveClass("relative");
    expect(await axeViolations(container)).toEqual([]);
  });

  it("sem parâmetros guardados (passados os 30 dias), não oferece reusar", async () => {
    renderHistory({ status: "succeeded", parameters: null });

    expect(within(await row()).queryByRole("link", { name: /^Reusar configuração/ })).toBeNull();
  });

  it("receita que a interface não conhece não oferece reusar", async () => {
    renderHistory({ status: "succeeded", recipe_id: "receita-futura" });

    expect(within(await row()).queryByRole("link", { name: /^Reusar configuração/ })).toBeNull();
  });
});
