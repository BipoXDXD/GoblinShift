import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { appRoutes } from "../test/appRoutes";
import { fakeApi, jsonResponse, runBody } from "../test/fakeApi";
import { renderRoutes } from "../test/renderRoutes";
import type { Run } from "../runs/api";

// Linhas sem link: o foco não acha a linha nova pelo `href`, como num `basename` futuro.
vi.mock("./RunList", () => ({
  RunList: ({ runs }: { readonly runs: readonly Run[] }) => (
    <ul>
      {runs.map((run) => (
        <li key={run.id}>{run.id}</li>
      ))}
    </ul>
  ),
}));

const RUN_A = "0b9c3c1e-7f62-4d1c-9a49-0d6f5d0b8f3a";
const RUN_B = "1c8d4e2f-0000-4000-8000-0000000000bb";

describe("Histórico sem a linha nova", () => {
  it("na última página, o foco vai para o título da tela", async () => {
    const api = fakeApi({
      "GET /api/v1/runs": (request) =>
        jsonResponse(
          new URL(request.url).searchParams.get("cursor") === "pagina-2"
            ? { data: [runBody({ id: RUN_B })], next_cursor: null }
            : { data: [runBody({ id: RUN_A })], next_cursor: "pagina-2" },
        ),
    });
    renderRoutes({ routes: appRoutes(api.client), path: "/historico", client: api.client });
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: "Carregar mais" }));

    await waitFor(() => {
      expect(screen.getByRole("heading", { level: 1, name: "Histórico" })).toHaveFocus();
    });
    expect(screen.getAllByRole("listitem")).toHaveLength(2);
  });
});
