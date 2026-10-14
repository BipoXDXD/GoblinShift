import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { z } from "zod";

import { createApiClient } from "../api/client";
import type { ContentInstructions, ReceivedContent } from "./api";
import { useUploadBatch, type UploadEntry } from "./useUploadBatch";
import type { ContentUploader, UploadContentOptions } from "./xhr";

const SHA = "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08";

let nextId = 0;
function uploadId(): string {
  nextId += 1;
  return `7d3c2a1e-0000-4000-8000-${String(nextId).padStart(12, "0")}`;
}

function intentFor(item: { name: string; size: number }) {
  const id = uploadId();
  return {
    id,
    name: item.name,
    size: item.size,
    state: "created",
    expires_at: "2026-10-15T12:00:00Z",
    content: {
      method: "PUT",
      url: `/api/v1/uploads/${id}/content`,
      headers: { "Content-Type": "application/octet-stream" },
    },
  };
}

const problem = (code: string, status: number) => ({
  type: "https://goblinshift.dev/problems/x",
  title: "t",
  status,
  detail: "d",
  instance: "/api/v1/uploads",
  code,
  request_id: "r",
});

const createUploadsBody = z.object({
  items: z.array(z.object({ name: z.string(), size: z.number() })),
});

/** API falsa: POST /uploads responde uma intenção por item, ou o problema configurado. */
function fakeApi() {
  const state: { failNextWith: { code: string; status: number } | null } = { failNextWith: null };
  const fetchMock = vi.fn<typeof fetch>((_input, init) => {
    const failure = state.failNextWith;
    if (failure !== null) {
      state.failNextWith = null;
      return Promise.resolve(
        new Response(JSON.stringify(problem(failure.code, failure.status)), {
          status: failure.status,
          headers: { "Content-Type": "application/problem+json" },
        }),
      );
    }
    const rawBody = typeof init?.body === "string" ? init.body : "";
    const { items } = createUploadsBody.parse(JSON.parse(rawBody));
    return Promise.resolve(
      new Response(JSON.stringify({ items: items.map(intentFor) }), {
        status: 201,
        headers: { "Content-Type": "application/json" },
      }),
    );
  });
  const client = createApiClient({ fetch: fetchMock, csrfToken: () => null, now: Date.now });
  return { client, fetchMock, state };
}

interface PendingUpload {
  readonly options: UploadContentOptions;
  readonly resolve: (value: ReceivedContent) => void;
  readonly reject: (reason: Error) => void;
}

/** Uploader falso: cada envio fica pendente até o teste resolvê-lo. */
function fakeUploader() {
  const pending: PendingUpload[] = [];
  const uploader: ContentUploader = {
    upload: (options) =>
      new Promise((resolve, reject) => {
        options.signal?.throwIfAborted();
        options.signal?.addEventListener("abort", () => {
          reject(new DOMException("cancelado", "AbortError"));
        });
        pending.push({ options, resolve, reject });
      }),
  };
  return { uploader, pending };
}

function receivedFor(content: ContentInstructions): ReceivedContent {
  const id = content.url.split("/")[4] ?? "";
  return { id, state: "ready", size: 4, sha256: SHA };
}

function file(name: string): File {
  return new File(["abcd"], name, { type: "text/csv" });
}

function setup() {
  const api = fakeApi();
  const { uploader, pending } = fakeUploader();
  const hook = renderHook(() =>
    useUploadBatch({
      client: api.client,
      uploader,
      rateLimit: { maxAttempts: 1, fallbackDelayMs: 0, random: () => 0 },
    }),
  );
  return { api, pending, hook };
}

function statuses(entries: readonly UploadEntry[]): string[] {
  return entries.map((entry) => entry.state.status);
}

describe("useUploadBatch", () => {
  it("cria as intenções e envia no máximo dois arquivos por vez", async () => {
    const { api, pending, hook } = setup();

    const files = [file("a.csv"), file("b.csv"), file("c.csv")];

    act(() => {
      hook.result.current.add(files);
    });

    expect(statuses(hook.result.current.entries)).toEqual(["waiting", "waiting", "waiting"]);
    await waitFor(() => {
      expect(pending).toHaveLength(2);
    });
    expect(api.fetchMock).toHaveBeenCalledTimes(1);
    expect(pending.map((upload) => upload.options.file)).toEqual(files.slice(0, 2));
  });

  it("mostra o progresso do envio e a espera da confirmação", async () => {
    const { pending, hook } = setup();
    act(() => {
      hook.result.current.add([file("a.csv")]);
    });
    await waitFor(() => {
      expect(pending).toHaveLength(1);
    });
    const upload = pending[0];
    if (upload === undefined) throw new Error("sem envio");

    act(() => {
      upload.options.onProgress({ phase: "sending", loadedBytes: 1, totalBytes: 4 });
    });
    expect(hook.result.current.entries[0]?.state).toEqual({
      status: "sending",
      loadedBytes: 1,
      totalBytes: 4,
    });

    act(() => {
      upload.options.onProgress({ phase: "confirming" });
    });
    expect(hook.result.current.entries[0]?.state).toEqual({ status: "confirming" });
  });

  it("marca pronto com o id e o hash do upload e começa o próximo", async () => {
    const { pending, hook } = setup();
    act(() => {
      hook.result.current.add([file("a.csv"), file("b.csv"), file("c.csv")]);
    });
    await waitFor(() => {
      expect(pending).toHaveLength(2);
    });
    const first = pending[0];
    if (first === undefined) throw new Error("sem envio");

    act(() => {
      first.resolve(receivedFor(first.options.content));
    });

    await waitFor(() => {
      expect(hook.result.current.entries[0]?.state).toEqual({
        status: "ready",
        uploadId: receivedFor(first.options.content).id,
        sha256: SHA,
      });
    });
    await waitFor(() => {
      expect(pending).toHaveLength(3);
    });
  });

  it("marca todos como falhos quando a API recusa o lote", async () => {
    const { api, hook } = setup();
    api.state.failNextWith = { code: "QUOTA_EXCEEDED", status: 429 };

    act(() => {
      hook.result.current.add([file("a.csv"), file("b.csv")]);
    });

    await waitFor(() => {
      expect(statuses(hook.result.current.entries)).toEqual(["failed", "failed"]);
    });
    const state = hook.result.current.entries[0]?.state;
    expect(state?.status === "failed" && state.failure.kind).toBe("problem");
  });

  it("refaz um arquivo falho com uma intenção nova", async () => {
    const { api, pending, hook } = setup();
    act(() => {
      hook.result.current.add([file("a.csv")]);
    });
    await waitFor(() => {
      expect(pending).toHaveLength(1);
    });
    act(() => {
      pending[0]?.reject(new Error("rede"));
    });
    await waitFor(() => {
      expect(statuses(hook.result.current.entries)).toEqual(["failed"]);
    });
    const localId = hook.result.current.entries[0]?.localId ?? "";

    act(() => {
      hook.result.current.retry(localId);
    });

    await waitFor(() => {
      expect(pending).toHaveLength(2);
    });
    expect(api.fetchMock).toHaveBeenCalledTimes(2);
    expect(pending[1]?.options.content.url).not.toBe(pending[0]?.options.content.url);
  });

  it("cancela um envio em andamento", async () => {
    const { pending, hook } = setup();
    act(() => {
      hook.result.current.add([file("a.csv")]);
    });
    await waitFor(() => {
      expect(pending).toHaveLength(1);
    });
    const localId = hook.result.current.entries[0]?.localId ?? "";

    act(() => {
      hook.result.current.cancel(localId);
    });

    expect(pending[0]?.options.signal?.aborted).toBe(true);
    await waitFor(() => {
      expect(statuses(hook.result.current.entries)).toEqual(["canceled"]);
    });
  });

  it("remove um arquivo da lista, abortando o envio", async () => {
    const { pending, hook } = setup();
    act(() => {
      hook.result.current.add([file("a.csv"), file("b.csv")]);
    });
    await waitFor(() => {
      expect(pending).toHaveLength(2);
    });
    const localId = hook.result.current.entries[0]?.localId ?? "";

    act(() => {
      hook.result.current.remove(localId);
    });

    expect(pending[0]?.options.signal?.aborted).toBe(true);
    expect(hook.result.current.entries.map((entry) => entry.file.name)).toEqual(["b.csv"]);
  });

  it("pede confirmação para sair da página só enquanto há envio", async () => {
    const { pending, hook } = setup();
    const leave = () => {
      const event = new Event("beforeunload", { cancelable: true });
      window.dispatchEvent(event);
      return event.defaultPrevented;
    };
    expect(leave()).toBe(false);

    act(() => {
      hook.result.current.add([file("a.csv")]);
    });
    await waitFor(() => {
      expect(pending).toHaveLength(1);
    });
    expect(leave()).toBe(true);

    const upload = pending[0];
    if (upload === undefined) throw new Error("sem envio");
    act(() => {
      upload.resolve(receivedFor(upload.options.content));
    });
    await waitFor(() => {
      expect(statuses(hook.result.current.entries)).toEqual(["ready"]);
    });
    expect(leave()).toBe(false);
  });

  it("cancela os envios ao desmontar", async () => {
    const { pending, hook } = setup();
    act(() => {
      hook.result.current.add([file("a.csv")]);
    });
    await waitFor(() => {
      expect(pending).toHaveLength(1);
    });

    hook.unmount();

    expect(pending[0]?.options.signal?.aborted).toBe(true);
  });
});
