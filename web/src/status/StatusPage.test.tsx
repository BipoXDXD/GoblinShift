import {
  defaultScheduler,
  focusManager,
  notifyManager,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import { act, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiClientProvider } from "../api/ApiClientContext";
import { appRoutes, preloadRoutes } from "../test/appRoutes";
import { axeViolations } from "../test/axe";
import { fakeApi, jsonResponse, problemResponse, type FakeApi } from "../test/fakeApi";
import { expectHeadingFocused } from "../test/focus";
import { renderRoutes } from "../test/renderRoutes";
import { StatusPage } from "./StatusPage";

await preloadRoutes("/estado");

const STATUS_ROUTE = "GET /api/v1/status";
const CHECKED_AT = "2026-11-28T12:30:00Z";

function statusBody(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return { api: "up", processing: "normal", queue: "free", checked_at: CHECKED_AT, ...overrides };
}

function renderPage(api: FakeApi): ReturnType<typeof render> {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <ApiClientProvider client={api.client}>
      <QueryClientProvider client={queryClient}>
        <StatusPage />
      </QueryClientProvider>
    </ApiClientProvider>,
  );
}

/** Pedidos ao /status (o fakeApi também registra o GET /me de quem monta a moldura). */
function statusRequests(api: FakeApi): number {
  return api.requests.filter((request) => new URL(request.url).pathname === "/api/v1/status")
    .length;
}

/** Cada linha: o rótulo (`dt`) e o valor (`dd`), que traz ícone e palavra. */
function rows(): { readonly label: string; readonly value: HTMLElement }[] {
  return screen.getAllByRole("definition").map((value) => ({
    label: value.previousElementSibling?.textContent ?? "",
    value,
  }));
}

afterEach(() => {
  focusManager.setFocused(undefined);
  notifyManager.setScheduler(defaultScheduler);
  vi.useRealTimers();
});

describe("/estado", () => {
  it("tudo normal mostra três linhas com ícone e texto", async () => {
    renderPage(fakeApi({ [STATUS_ROUTE]: () => jsonResponse(statusBody()) }));

    expect(await screen.findByText("No ar")).toBeInTheDocument();
    const found = rows();
    expect(found.map(({ label, value }) => [label, value.textContent])).toEqual([
      ["API", "No ar"],
      ["Processamento", "Normal"],
      ["Fila", "Livre"],
    ]);
    for (const { value } of found) {
      // Cor nunca é o único sinal: cada valor tem o ícone (decorativo) e a palavra.
      expect(value.querySelector("svg[aria-hidden='true']")).not.toBeNull();
    }
  });

  it.each([
    [{ processing: "slow", queue: "busy" }, "Lento", "Ocupada"],
    [{ processing: "stalled", queue: "full" }, "Parado", "Cheia"],
  ])("os níveis %o viram texto", async (levels, processing, queue) => {
    renderPage(fakeApi({ [STATUS_ROUTE]: () => jsonResponse(statusBody(levels)) }));

    expect(await screen.findByText(processing)).toBeInTheDocument();
    expect(screen.getByText(queue)).toBeInTheDocument();
  });

  it("diz a hora da leitura e que a página cai junto com o servidor", async () => {
    renderPage(fakeApi({ [STATUS_ROUTE]: () => jsonResponse(statusBody()) }));

    const time = new Intl.DateTimeFormat("pt-BR", { timeStyle: "short" }).format(
      new Date(CHECKED_AT),
    );
    expect(await screen.findByText(`Lido às ${time}`)).toBeInTheDocument();
    expect(
      screen.getByText(
        "Esta página roda no mesmo servidor do GoblinShift: se ele cair inteiro, ela cai junto.",
      ),
    ).toBeInTheDocument();
  });

  it("503 vira API fora do ar, sem quebrar a página", async () => {
    renderPage(
      fakeApi({
        [STATUS_ROUTE]: () =>
          problemResponse(503, "SERVICE_UNAVAILABLE", { headers: { "Retry-After": "5" } }),
      }),
    );

    expect(await screen.findByText("Fora do ar")).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1, name: "Estado do serviço" })).toBeVisible();
    expect(rows().map(({ value }) => value.textContent)).toEqual([
      "Fora do ar",
      "Sem leitura",
      "Sem leitura",
    ]);
    expect(screen.queryByText(/^Lido às/)).toBeNull();
  });

  it("falha de rede também vira API fora do ar", async () => {
    renderPage(fakeApi({ [STATUS_ROUTE]: () => Promise.reject(new TypeError("Failed to fetch")) }));

    expect(await screen.findByText("Fora do ar")).toBeInTheDocument();
  });

  it.each([500, 502])("%i também vira API fora do ar", async (status) => {
    renderPage(fakeApi({ [STATUS_ROUTE]: () => problemResponse(status, "SERVICE_UNAVAILABLE") }));

    expect(await screen.findByText("Fora do ar")).toBeInTheDocument();
  });

  it.each([
    [
      "429 (a API respondeu, só recusou)",
      () => problemResponse(429, "RATE_LIMITED", { headers: { "Retry-After": "1" } }),
    ],
    ["corpo fora da spec", () => jsonResponse(statusBody({ processing: "dormindo" }))],
  ])("%s fica sem leitura, no tom neutro, sem dizer que caiu", async (_case, handler) => {
    renderPage(fakeApi({ [STATUS_ROUTE]: handler }));

    expect(await screen.findAllByText("Sem leitura")).toHaveLength(3);
    expect(screen.queryByText("Fora do ar")).toBeNull();
    for (const { value } of rows()) {
      expect(value.firstElementChild).toHaveClass("bg-info-bg");
    }
  });

  it("relê a cada 30 s só com a aba visível", async () => {
    vi.useFakeTimers();
    notifyManager.setScheduler((callback) => {
      callback();
    });
    const api = fakeApi(
      { [STATUS_ROUTE]: () => jsonResponse(statusBody()) },
      { now: () => Date.now() },
    );
    renderPage(api);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(statusRequests(api)).toBe(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000);
    });
    expect(statusRequests(api)).toBe(2);

    act(() => {
      focusManager.setFocused(false);
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(90_000);
    });
    expect(statusRequests(api)).toBe(2);
  });

  it("não tem violação do axe", async () => {
    const { container } = renderPage(
      fakeApi({ [STATUS_ROUTE]: () => jsonResponse(statusBody({ processing: "stalled" })) }),
    );
    await screen.findByText("Parado");

    expect(await axeViolations(container)).toEqual([]);
  });
});

describe("rota /estado", () => {
  it("abre sem sessão, com o foco no título, e o rodapé leva a ela", async () => {
    const api = fakeApi(
      {
        "GET /api/v1/me": () => problemResponse(401, "AUTHENTICATION_REQUIRED"),
        [STATUS_ROUTE]: () => jsonResponse(statusBody()),
      },
      { csrfToken: null },
    );
    renderRoutes({ routes: appRoutes(api.client), path: "/estado", client: api.client });

    await expectHeadingFocused("Estado do serviço");
    expect(await screen.findByText("No ar")).toBeInTheDocument();
    const footer = screen.getByRole("contentinfo");
    expect(within(footer).getByRole("link", { name: "Estado do serviço" })).toHaveAttribute(
      "href",
      "/estado",
    );
  });
});
