import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, type ApiFailure } from "../api/response";
import { CrossOriginUrlError } from "../api/sameOrigin";
import { FakeXhr } from "../test/fakeXhr";
import { createContentUploader, type UploadProgress } from "./xhr";

const NOW_MS = Date.parse("2026-10-14T12:00:00Z");
const content = {
  method: "PUT",
  url: "/api/v1/uploads/7d3c2a1e-0000-4000-8000-000000000001/content",
  headers: { "Content-Type": "application/octet-stream" },
} as const;
const received = {
  id: "7d3c2a1e-0000-4000-8000-000000000001",
  state: "ready",
  size: 4,
  sha256: "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
};
const rateLimited = {
  type: "https://goblinshift.dev/problems/rate-limited",
  title: "Muitas requisições",
  status: 429,
  detail: "Limite de transferências simultâneas atingido.",
  instance: content.url,
  code: "RATE_LIMITED",
  request_id: "0b9c3c1e-7f62-4d1c-9a49-0d6f5d0b8f3a",
};

function setup(csrfToken: string | null = null, url: string = content.url) {
  const xhr = new FakeXhr();
  const uploader = createContentUploader({
    createXhr: () => xhr,
    csrfToken: () => csrfToken,
    now: () => NOW_MS,
  });
  const progress: UploadProgress[] = [];
  const file = new Blob(["abcd"]);
  const start = (signal?: AbortSignal) =>
    uploader.upload({
      content: { ...content, url },
      file,
      onProgress: (event) => progress.push(event),
      ...(signal === undefined ? {} : { signal }),
    });
  return { xhr, file, progress, start };
}

async function failureOf(promise: Promise<unknown>): Promise<ApiFailure> {
  const error: unknown = await promise.then(
    () => null,
    (reason: unknown) => reason,
  );
  if (!(error instanceof ApiError)) {
    throw new Error(`esperava ApiError, veio ${String(error)}`);
  }
  return error.failure;
}

describe("upload de conteúdo por XHR: pedido", () => {
  it("usa método, URL e headers da intenção como vieram e envia o arquivo", () => {
    const { xhr, file, start } = setup();

    void start();

    expect(xhr.method).toBe("PUT");
    expect(xhr.url).toBe(new URL(content.url, window.location.origin).href);
    expect(xhr.requestHeaders.get("Content-Type")).toBe("application/octet-stream");
    expect(xhr.requestHeaders.get("Accept")).toBe("application/json, application/problem+json");
    expect(xhr.body).toBe(file);
  });

  it("manda o CSRF quando há sessão", () => {
    const { xhr, start } = setup("tok");

    void start();

    expect(xhr.requestHeaders.get("X-CSRF-Token")).toBe("tok");
  });

  it.each([
    "https://evil.example/api/v1/uploads/1/content",
    "//evil.example/api/v1/uploads/1/content",
  ])("recusa URL de outra origem (%s) sem abrir o envio nem expor o CSRF", async (url) => {
    const { xhr, start } = setup("tok", url);

    await expect(start()).rejects.toBeInstanceOf(CrossOriginUrlError);
    expect(xhr.method).toBeNull();
    expect(xhr.requestHeaders.size).toBe(0);
  });

  it("não inventa CSRF sem sessão", () => {
    const { xhr, start } = setup(null);

    void start();

    expect(xhr.requestHeaders.has("X-CSRF-Token")).toBe(false);
  });
});

describe("upload de conteúdo por XHR: progresso", () => {
  it("informa os bytes enviados do tamanho do arquivo", () => {
    const { xhr, progress, start } = setup();
    void start();

    xhr.progress(1, 4);

    expect(progress).toEqual([{ phase: "sending", loadedBytes: 1, totalBytes: 4 }]);
  });

  it("passa a confirmando quando o último byte sai, antes da resposta", () => {
    const { xhr, progress, start } = setup();
    void start();

    xhr.progress(4, 4);

    expect(progress).toEqual([{ phase: "confirming" }]);
  });
});

describe("upload de conteúdo por XHR: resposta", () => {
  it("resolve com o conteúdo recebido já validado", async () => {
    const { xhr, start } = setup();
    const pending = start();

    xhr.respond(200, { ...received, extra: true }, { "Content-Type": "application/json" });

    await expect(pending).resolves.toEqual(received);
  });

  it("traduz 429 em problema com Retry-After", async () => {
    const { xhr, start } = setup();
    const pending = failureOf(start());

    xhr.respond(429, rateLimited, {
      "Content-Type": "application/problem+json",
      "Retry-After": "2",
    });

    expect(await pending).toEqual({
      kind: "problem",
      status: 429,
      problem: rateLimited,
      retryAfterMs: 2_000,
    });
  });

  it("recusa corpo de sucesso fora do contrato", async () => {
    const { xhr, start } = setup();
    const pending = failureOf(start());

    xhr.respond(200, { ...received, sha256: 1 });

    expect(await pending).toEqual({
      kind: "unexpected-response",
      status: 200,
      retryAfterMs: null,
    });
  });

  it("traduz falha de rede", async () => {
    const { xhr, start } = setup();
    const pending = failureOf(start());

    xhr.failNetwork();

    expect(await pending).toEqual({ kind: "network" });
  });
});

describe("upload de conteúdo por XHR: cancelamento", () => {
  it("aborta o envio e rejeita com o motivo do cancelamento", async () => {
    const { xhr, start } = setup();
    const controller = new AbortController();
    const pending = start(controller.signal);

    controller.abort();

    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
    expect(xhr.aborted).toBe(true);
  });

  it("não abre o envio com o sinal já cancelado", async () => {
    const { xhr, start } = setup();
    const send = vi.spyOn(xhr, "send");

    await expect(start(AbortSignal.abort())).rejects.toMatchObject({ name: "AbortError" });
    expect(send).not.toHaveBeenCalled();
  });
});

describe("upload de conteúdo por XHR: inatividade", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("aborta e rejeita com timeout depois de 45 s sem progresso nem resposta", async () => {
    vi.useFakeTimers();
    const { xhr, start } = setup();
    const pending = failureOf(start());

    await vi.advanceTimersByTimeAsync(44_999);
    expect(xhr.aborted).toBe(false);
    await vi.advanceTimersByTimeAsync(1);

    expect(await pending).toEqual({ kind: "timeout" });
    expect(xhr.aborted).toBe(true);
  });

  it("reinicia o prazo a cada progresso", async () => {
    vi.useFakeTimers();
    const { xhr, start } = setup();
    const pending = failureOf(start());

    await vi.advanceTimersByTimeAsync(44_000);
    xhr.progress(1, 4);
    await vi.advanceTimersByTimeAsync(44_999);
    expect(xhr.aborted).toBe(false);
    await vi.advanceTimersByTimeAsync(1);

    expect(await pending).toEqual({ kind: "timeout" });
  });

  it("conta o prazo também na espera pela confirmação, depois do último byte", async () => {
    vi.useFakeTimers();
    const { xhr, start } = setup();
    const pending = failureOf(start());

    xhr.progress(4, 4);
    await vi.advanceTimersByTimeAsync(45_000);

    expect(await pending).toEqual({ kind: "timeout" });
  });

  it("não aborta depois que a resposta chega", async () => {
    vi.useFakeTimers();
    const { xhr, start } = setup();
    const pending = start();

    xhr.respond(200, received, { "Content-Type": "application/json" });
    await vi.advanceTimersByTimeAsync(60_000);

    await expect(pending).resolves.toEqual(received);
    expect(xhr.aborted).toBe(false);
  });

  it("não aborta depois de uma falha de rede", async () => {
    vi.useFakeTimers();
    const { xhr, start } = setup();
    const pending = failureOf(start());

    xhr.failNetwork();
    await vi.advanceTimersByTimeAsync(60_000);

    expect(await pending).toEqual({ kind: "network" });
    expect(xhr.aborted).toBe(false);
  });

  it("cancelamento de quem chamou continua saindo como cancelamento, não como timeout", async () => {
    vi.useFakeTimers();
    const { start } = setup();
    const controller = new AbortController();
    const rejection = expect(start(controller.signal)).rejects.toMatchObject({
      name: "AbortError",
    });

    await vi.advanceTimersByTimeAsync(10_000);
    controller.abort();
    await vi.advanceTimersByTimeAsync(60_000);

    await rejection;
  });
});
