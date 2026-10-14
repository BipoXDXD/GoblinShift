import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../api/response";
import { createLimiter, retryOnRateLimit, type RateLimitPolicy } from "./queue";

function deferred<T>() {
  let resolve: (value: T) => void = () => undefined;
  let reject: (reason: Error) => void = () => undefined;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function problemError(code: "RATE_LIMITED" | "QUOTA_EXCEEDED", retryAfterMs: number | null) {
  return new ApiError({
    kind: "problem",
    status: 429,
    retryAfterMs,
    problem: {
      type: "https://goblinshift.dev/problems/x",
      title: "t",
      status: 429,
      detail: "d",
      instance: "/i",
      code,
      request_id: "r",
    },
  });
}

describe("createLimiter", () => {
  it("roda no máximo o limite de tarefas ao mesmo tempo", () => {
    const limit = createLimiter(2);
    const started: number[] = [];
    const tasks = [deferred<string>(), deferred<string>(), deferred<string>()];

    tasks.forEach((task, index) => {
      void limit(() => {
        started.push(index);
        return task.promise;
      });
    });

    expect(started).toEqual([0, 1]);
  });

  it("começa a próxima quando uma termina", async () => {
    const limit = createLimiter(2);
    const started: number[] = [];
    const tasks = [deferred<string>(), deferred<string>(), deferred<string>()];
    const runs = tasks.map((task, index) =>
      limit(() => {
        started.push(index);
        return task.promise;
      }),
    );

    tasks[0]?.resolve("feito");
    await runs[0];

    expect(started).toEqual([0, 1, 2]);
  });

  it("libera a vaga quando a tarefa falha e repassa a falha", async () => {
    const limit = createLimiter(1);
    const first = limit(() => Promise.reject(new Error("falhou")));
    const second = limit(() => Promise.resolve("ok"));

    await expect(first).rejects.toThrow("falhou");
    await expect(second).resolves.toBe("ok");
  });

  it.each([0, -1, 1.5])("recusa limite inválido %d", (value) => {
    expect(() => createLimiter(value)).toThrow(RangeError);
  });
});

describe("retryOnRateLimit", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  const policy: RateLimitPolicy = { maxAttempts: 3, fallbackDelayMs: 2_000, random: () => 0 };

  it("devolve o resultado sem esperar quando a primeira tentativa passa", async () => {
    const attempt = vi.fn(() => Promise.resolve("ok"));

    await expect(retryOnRateLimit(attempt, policy)).resolves.toBe("ok");
    expect(attempt).toHaveBeenCalledTimes(1);
  });

  it("espera o Retry-After antes de tentar de novo, nunca antes", async () => {
    vi.useFakeTimers();
    const attempt = vi
      .fn<() => Promise<string>>()
      .mockRejectedValueOnce(problemError("RATE_LIMITED", 5_000))
      .mockResolvedValueOnce("ok");

    const pending = retryOnRateLimit(attempt, policy);
    await vi.advanceTimersByTimeAsync(4_999);
    expect(attempt).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);

    await expect(pending).resolves.toBe("ok");
    expect(attempt).toHaveBeenCalledTimes(2);
  });

  it("acrescenta jitter só para cima do Retry-After", async () => {
    vi.useFakeTimers();
    const attempt = vi
      .fn<() => Promise<string>>()
      .mockRejectedValueOnce(problemError("RATE_LIMITED", 1_000))
      .mockResolvedValueOnce("ok");

    const pending = retryOnRateLimit(attempt, { ...policy, random: () => 0.5 });
    await vi.advanceTimersByTimeAsync(1_049);
    expect(attempt).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);

    await expect(pending).resolves.toBe("ok");
  });

  it("usa o intervalo padrão quando o 429 vem sem Retry-After", async () => {
    vi.useFakeTimers();
    const attempt = vi
      .fn<() => Promise<string>>()
      .mockRejectedValueOnce(problemError("RATE_LIMITED", null))
      .mockResolvedValueOnce("ok");

    const pending = retryOnRateLimit(attempt, policy);
    await vi.advanceTimersByTimeAsync(2_000);

    await expect(pending).resolves.toBe("ok");
  });

  it("desiste depois do teto de tentativas com o último erro", async () => {
    vi.useFakeTimers();
    const limited = problemError("RATE_LIMITED", 1_000);
    const attempt = vi.fn(() => Promise.reject(limited));

    const pending = expect(retryOnRateLimit(attempt, policy)).rejects.toBe(limited);
    await vi.advanceTimersByTimeAsync(10_000);

    await pending;
    expect(attempt).toHaveBeenCalledTimes(3);
  });

  it.each([
    ["cota excedida", problemError("QUOTA_EXCEEDED", 1_000)],
    ["falha de rede", new ApiError({ kind: "network" })],
    ["erro qualquer", new Error("x")],
  ])("não repete %s", async (_label, error) => {
    const attempt = vi.fn(() => Promise.reject(error));

    await expect(retryOnRateLimit(attempt, policy)).rejects.toBe(error);
    expect(attempt).toHaveBeenCalledTimes(1);
  });

  it("para de esperar quando o sinal é cancelado", async () => {
    vi.useFakeTimers();
    const controller = new AbortController();
    const attempt = vi.fn(() => Promise.reject(problemError("RATE_LIMITED", 60_000)));

    const pending = retryOnRateLimit(attempt, { ...policy, signal: controller.signal });
    await vi.advanceTimersByTimeAsync(1);
    controller.abort();

    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
    expect(attempt).toHaveBeenCalledTimes(1);
  });
});
