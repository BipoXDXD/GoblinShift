import { act, render, screen, within } from "@testing-library/react";
import { describe, expect, it, onTestFinished, vi } from "vitest";

import { installAnnounceClock, untilFakeClock } from "../test/announceClock";
import { preloadRoutes } from "../test/appRoutes";
import { jsonResponse, problemResponse, runBody } from "../test/fakeApi";
import { TEXT } from "../text";

import { App } from "./App";

const HOUR_MS = 60 * 60 * 1_000;

describe("App", () => {
  it("renderiza a rota inicial", async () => {
    window.history.pushState({}, "", "/");

    render(<App />);

    expect(
      await screen.findByRole("heading", {
        level: 1,
        name: "Arquivos em lote, limpos pelas goblins da oficina.",
      }),
    ).toBeInTheDocument();
  });

  it("toda tela tem a marca levando à Oficina e o atalho para o conteúdo", () => {
    window.history.pushState({}, "", "/rota-que-nao-existe");

    render(<App />);

    expect(screen.getByRole("link", { name: "GoblinShift" })).toHaveAttribute("href", "/");
    expect(screen.getByRole("link", { name: "Pular para o conteúdo" })).toHaveAttribute(
      "href",
      `#${screen.getByRole("main").id}`,
    );
  });

  // A altura medida de fato fica no e2e (controls.spec.ts); aqui, o aviso rápido de que a classe
  // do alvo de toque e a centralização vertical não sumiram.
  it("o atalho para o conteúdo tem o alvo de toque de 44 px", () => {
    window.history.pushState({}, "", "/rota-que-nao-existe");

    render(<App />);

    expect(screen.getByRole("link", { name: "Pular para o conteúdo" })).toHaveClass(
      "focus:min-h-11",
      "focus:inline-flex",
      "items-center",
    );
  });

  it("a rota inicial leva à Bancada de cada receita", async () => {
    window.history.pushState({}, "", "/");

    render(<App />);
    await screen.findByRole("heading", { level: 1 });

    expect(
      within(screen.getByRole("main"))
        .getAllByRole("link")
        .map((link) => [link.textContent, link.getAttribute("href")]),
    ).toEqual([
      // A ação primária da abertura (D4) e o atalho para os cartões vêm antes deles.
      ["Testar com um exemplo", "/bancada/image-forge?exemplo=1"],
      ["ou escolha uma receita", "#receitas"],
      ["Abrir a Bancada da CSV Janitor", "/bancada/csv-janitor"],
      ["Abrir a Bancada da Image Forge", "/bancada/image-forge"],
      ["Abrir a Bancada da Downloads Detox", "/bancada/downloads-detox"],
      ["Abrir a Bancada da PDF Press", "/bancada/pdf-press"],
      ["Abrir a Bancada da Media Mill", "/bancada/media-mill"],
    ]);
  });

  it("o nome acessível do link de cada cartão separa as palavras", async () => {
    window.history.pushState({}, "", "/");

    render(<App />);

    expect(
      await screen.findByRole("link", { name: "Abrir a Bancada da CSV Janitor" }),
    ).toHaveAttribute("href", "/bancada/csv-janitor");
  });

  it("aguarda Retry-After nas consultas e só mostra o alerta após a última falha", async () => {
    await preloadRoutes("/bancada/csv-janitor");
    installAnnounceClock();
    const catalog = vi.fn(() =>
      Promise.resolve(
        problemResponse(503, "SERVICE_UNAVAILABLE", { headers: { "Retry-After": "30" } }),
      ),
    );
    const fetchMock = vi.fn<typeof fetch>((input) =>
      new Request(input).url.endsWith("/api/v1/catalog")
        ? catalog()
        : Promise.resolve(problemResponse(401, "AUTHENTICATION_REQUIRED")),
    );
    vi.stubGlobal("fetch", fetchMock);
    window.history.pushState({}, "", "/bancada/csv-janitor");

    render(<App />);

    await untilFakeClock(() => {
      expect(catalog).toHaveBeenCalledTimes(1);
      expect(screen.getByText("Carregando a receita.")).toBeVisible();
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(29_999);
    });
    expect(catalog).toHaveBeenCalledTimes(1);

    for (const [delay, calls] of [
      [1, 2],
      [30_000, 3],
      [30_000, 4],
    ] as const) {
      expect(screen.queryByText(/serviço está indisponível/i)).toBeNull();
      await act(async () => {
        await vi.advanceTimersByTimeAsync(delay);
      });
      expect(catalog).toHaveBeenCalledTimes(calls);
    }

    await untilFakeClock(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(/serviço está indisponível/i);
    });
  });

  it("sem login, a Bancada pede para entrar sem repetir o pedido do catálogo", async () => {
    const fetchMock = vi.fn<typeof fetch>(() =>
      Promise.resolve(problemResponse(401, "AUTHENTICATION_REQUIRED")),
    );
    vi.stubGlobal("fetch", fetchMock);
    window.history.pushState({}, "", "/bancada/csv-janitor");

    render(<App />);

    const prompt = await screen.findByRole("region", { name: "Entre para continuar" });
    expect(prompt).toHaveTextContent("Entre para enviar arquivos e ver suas execuções.");
    expect(within(prompt).getByRole("link", { name: "Entrar com Google" })).toHaveAttribute(
      "href",
      "/auth/login/google?return_to=%2Fbancada%2Fcsv-janitor",
    );
    const catalogCalls = fetchMock.mock.calls.filter(([input]) =>
      new Request(input).url.endsWith("/api/v1/catalog"),
    );
    expect(catalogCalls).toHaveLength(1);
  });

  it("o histórico diz expirado pelo relógio do servidor quando o local está 3 h atrasado", async () => {
    await preloadRoutes("/historico");
    const localMs = Date.parse("2026-10-14T12:00:00Z");
    vi.useFakeTimers({ toFake: ["Date"] });
    onTestFinished(() => {
      vi.useRealTimers();
    });
    vi.setSystemTime(localMs);
    const serverDate = new Date(localMs + 3 * HOUR_MS).toUTCString();
    const fetchMock = vi.fn<typeof fetch>((input) => {
      const response = new Request(input).url.includes("/api/v1/runs?")
        ? jsonResponse({
            data: [
              runBody({
                status: "succeeded",
                results_expire_at: new Date(localMs + HOUR_MS).toISOString(),
              }),
            ],
            next_cursor: null,
          })
        : problemResponse(401, "AUTHENTICATION_REQUIRED");
      response.headers.set("Date", serverDate);
      return Promise.resolve(response);
    });
    vi.stubGlobal("fetch", fetchMock);
    window.history.pushState({}, "", "/historico");

    render(<App />);

    expect(await screen.findByText(TEXT.history.availability.expired)).toBeInTheDocument();
  });

  it("mostra página não encontrada para rota desconhecida", () => {
    window.history.pushState({}, "", "/rota-que-nao-existe");

    render(<App />);

    expect(
      screen.getByRole("heading", { level: 1, name: "Página não encontrada" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText("O endereço pode estar errado, ou a página saiu do ar."),
    ).toBeInTheDocument();
  });
});
