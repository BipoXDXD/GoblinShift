import {
  focusManager,
  notifyManager,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import { act, renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiClientProvider } from "../api/ApiClientContext";
import type { ApiClient, ApiRequest } from "../api/client";
import { ApiError, type ApiFailure, type ApiResponse } from "../api/response";
import type { Run, RunPhase, RunStatus } from "./api";
import { useRunPolling } from "./useRunPolling";

const RUN_ID = "0e9a1c52-0000-4000-8000-000000000001";

function run(status: RunStatus, phase: RunPhase | null = null): Run {
  return {
    id: RUN_ID,
    recipe_id: "csv-janitor",
    recipe_version: 1,
    status,
    attempt: 1,
    progress: { phase, processed: 0, total: 1 },
    queue_ahead: 0,
    created_at: "2026-10-14T12:00:00Z",
    queue_expires_at: "2026-10-14T13:00:00Z",
    deadline_at: null,
    error: null,
    parameters: {},
    preset_id: null,
    results_expire_at: null,
  };
}

type Outcome = { run: Run; retryAfterMs: number | null } | { failure: ApiFailure };

/**
 * Cliente falso que responde em sequência (a última resposta se repete). Dispensa o fetch: o corpo
 * do Response do Node é lido fora dos relógios falsos.
 */
function fakeClient(outcomes: readonly (() => Outcome)[]) {
  let served = 0;
  const request = <T,>(apiRequest: ApiRequest<T>): Promise<ApiResponse<T>> => {
    const next = outcomes[Math.min(served, outcomes.length - 1)];
    served += 1;
    if (next === undefined) {
      throw new Error("sem resposta configurada");
    }
    const outcome = next();
    if ("failure" in outcome) {
      return Promise.reject(new ApiError(outcome.failure));
    }
    return Promise.resolve({
      data: apiRequest.schema.parse(outcome.run),
      status: 200,
      retryAfterMs: outcome.retryAfterMs,
    });
  };
  const client: ApiClient = { request, hasSessionCookie: () => true };
  return { client, requestCount: () => served };
}

function setup(client: ApiClient) {
  // O TanStack só renderiza de novo pelas propriedades lidas na renderização; aqui quem lê é o
  // teste, depois dela, então todas contam.
  const queryClient = new QueryClient({
    defaultOptions: { queries: { notifyOnChangeProps: "all" } },
  });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <ApiClientProvider client={client}>
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    </ApiClientProvider>
  );
  return renderHook(() => useRunPolling(RUN_ID, { random: () => 0 }), { wrapper });
}

function failureOf(error: Error | null) {
  return error instanceof ApiError ? error.failure : null;
}

async function advance(ms: number): Promise<void> {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

beforeEach(() => {
  vi.useFakeTimers();
  // O TanStack Query entrega as atualizações num setTimeout(0); com relógios falsos, entregar na
  // hora mantém cada avanço do relógio como um passo só.
  notifyManager.setScheduler((callback) => {
    callback();
  });
});

afterEach(() => {
  focusManager.setFocused(undefined);
  notifyManager.setScheduler((callback) => setTimeout(callback, 0));
  vi.useRealTimers();
});

describe("useRunPolling", () => {
  it("consulta de novo só quando o Retry-After vence", async () => {
    const { client, requestCount } = fakeClient([
      () => ({ run: run("running", "processing"), retryAfterMs: 2_000 }),
    ]);
    const { result } = setup(client);
    await advance(0);
    expect(result.current.query.data?.status).toBe("running");
    expect(requestCount()).toBe(1);

    await advance(1_999);
    expect(requestCount()).toBe(1);

    await advance(1);
    expect(requestCount()).toBe(2);
  });

  it("respeita um Retry-After maior que o padrão", async () => {
    const { client, requestCount } = fakeClient([
      () => ({ run: run("queued"), retryAfterMs: 5_000 }),
    ]);
    setup(client);
    await advance(0);

    await advance(4_999);
    expect(requestCount()).toBe(1);

    await advance(1);
    expect(requestCount()).toBe(2);
  });

  it("fica parado com a aba oculta e consulta ao voltar se a espera já passou", async () => {
    const { client, requestCount } = fakeClient([
      () => ({ run: run("running", "processing"), retryAfterMs: 2_000 }),
    ]);
    setup(client);
    await advance(0);

    act(() => {
      focusManager.setFocused(false);
    });
    await advance(10_000);
    expect(requestCount()).toBe(1);

    act(() => {
      focusManager.setFocused(true);
    });
    await advance(0);
    expect(requestCount()).toBe(2);
  });

  it("não antecipa a consulta ao voltar para a aba antes do Retry-After", async () => {
    const { client, requestCount } = fakeClient([
      () => ({ run: run("running", "processing"), retryAfterMs: 2_000 }),
    ]);
    setup(client);
    await advance(0);

    act(() => {
      focusManager.setFocused(false);
    });
    await advance(500);
    act(() => {
      focusManager.setFocused(true);
    });
    await advance(0);

    expect(requestCount()).toBe(1);
  });

  it("para de consultar no estado terminal", async () => {
    const { client, requestCount } = fakeClient([
      () => ({ run: run("running", "finalizing"), retryAfterMs: 2_000 }),
      () => ({ run: run("succeeded"), retryAfterMs: null }),
    ]);
    const { result } = setup(client);
    await advance(0);
    await advance(2_000);
    expect(result.current.query.data?.status).toBe("succeeded");

    await advance(60_000);

    expect(requestCount()).toBe(2);
  });

  it("anuncia só a mudança de status ou fase", async () => {
    const { client } = fakeClient([
      () => ({ run: run("running", "inspecting"), retryAfterMs: 2_000 }),
      () => ({ run: run("running", "inspecting"), retryAfterMs: 2_000 }),
      () => ({ run: run("running", "processing"), retryAfterMs: 2_000 }),
    ]);
    const { result } = setup(client);
    await advance(0);
    expect(result.current.announcement).toBeNull();

    await advance(2_000);
    expect(result.current.announcement).toBeNull();

    await advance(2_000);
    expect(result.current.announcement).toBe("Processando os arquivos.");
  });

  it("recua depois de falha de rede e volta ao normal no sucesso", async () => {
    let failing = true;
    const { client, requestCount } = fakeClient([
      (): Outcome =>
        failing ? { failure: { kind: "network" } } : { run: run("running"), retryAfterMs: 2_000 },
    ]);
    const { result } = setup(client);
    await advance(0);
    expect(failureOf(result.current.query.error)).toEqual({ kind: "network" });

    await advance(2_000);
    expect(requestCount()).toBe(2);
    await advance(3_999);
    expect(requestCount()).toBe(2);
    failing = false;
    await advance(1);
    expect(requestCount()).toBe(3);
    expect(result.current.query.error).toBeNull();

    await advance(2_000);
    expect(requestCount()).toBe(4);
  });

  it("para quando a execução não existe", async () => {
    const notFound = {
      type: "https://goblinshift.dev/problems/not-found",
      title: "Recurso não encontrado",
      status: 404,
      detail: "Execução não encontrada.",
      instance: `/api/v1/runs/${RUN_ID}`,
      code: "NOT_FOUND",
      request_id: "r",
    } as const;
    const { client, requestCount } = fakeClient([
      (): Outcome => ({
        failure: { kind: "problem", status: 404, problem: notFound, retryAfterMs: null },
      }),
    ]);
    const { result } = setup(client);
    await advance(0);
    expect(failureOf(result.current.query.error)?.kind).toBe("problem");

    await advance(60_000);

    expect(requestCount()).toBe(1);
  });
});
