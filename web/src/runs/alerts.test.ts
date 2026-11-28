import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import {
  installFakeNotification,
  installFakeServiceWorker,
  setDocumentVisibility,
} from "../test/fakeNotification";
import { ALERTS_KEY, notifyCompletion, requestAlerts, useCompletionAlert } from "./alerts";
import type { RunDetail, RunItem, RunStatus } from "./api";

const RUN_ID = "0e9a1c52-0000-4000-8000-000000000008";

function run(status: RunStatus): RunDetail {
  const item = (position: number): RunItem => ({
    position,
    upload_id: `0e9a1c52-0000-4000-8000-00000000010${String(position)}`,
    original_name: `foto-${String(position)}.png`,
    status: "ok",
    error_code: null,
    warnings: [],
    notices: [],
    counts: {},
    duplicate_of: null,
    formula_cells: null,
    comparison: null,
  });
  return {
    id: RUN_ID,
    recipe_id: "image-forge",
    recipe_version: 1,
    status,
    attempt: 1,
    progress: { phase: null, processed: 3, total: 3 },
    queue_ahead: 0,
    created_at: "2026-11-28T12:00:00Z",
    queue_expires_at: "2026-11-28T13:00:00Z",
    deadline_at: null,
    error: null,
    parameters: {},
    preset_id: null,
    results_expire_at: null,
    summary: null,
    items: [item(0), item(1), item(2)],
  };
}

const INPUT = {
  runId: RUN_ID,
  status: "succeeded",
  recipeTitle: "Image Forge",
  fileCount: 3,
} as const;

describe("notifyCompletion", () => {
  it("não avisa com a aba visível", async () => {
    const { constructed } = installFakeNotification("granted");
    installFakeServiceWorker("absent");
    window.localStorage.setItem(ALERTS_KEY, "on");

    await notifyCompletion(INPUT);

    expect(constructed).toHaveLength(0);
  });

  it("não avisa sem o aviso ligado", async () => {
    const { constructed } = installFakeNotification("granted");
    installFakeServiceWorker("absent");
    setDocumentVisibility("hidden");

    await notifyCompletion(INPUT);

    expect(constructed).toHaveLength(0);
  });

  it("avisa com a tag da execução e sem nome de arquivo", async () => {
    const { constructed } = installFakeNotification("granted");
    installFakeServiceWorker("absent");
    window.localStorage.setItem(ALERTS_KEY, "on");
    setDocumentVisibility("hidden");

    await notifyCompletion(INPUT);

    expect(constructed).toEqual([
      {
        title: "Execução concluída",
        options: {
          body: "Image Forge · 3 arquivos",
          tag: RUN_ID,
          data: { url: `/execucoes/${RUN_ID}` },
        },
      },
    ]);
  });

  it("usa o service worker quando registrado", async () => {
    const { constructed } = installFakeNotification("granted");
    const shown = installFakeServiceWorker("registered");
    window.localStorage.setItem(ALERTS_KEY, "on");
    setDocumentVisibility("hidden");

    await notifyCompletion({ ...INPUT, status: "failed", fileCount: 1 });

    expect(constructed).toHaveLength(0);
    expect(shown).toEqual([
      {
        title: "Execução falhou",
        options: {
          body: "Image Forge · 1 arquivo",
          tag: RUN_ID,
          data: { url: `/execucoes/${RUN_ID}` },
        },
      },
    ]);
  });

  it("erro do service worker é engolido, sem console", async () => {
    const { constructed } = installFakeNotification("granted");
    installFakeServiceWorker("broken");
    window.localStorage.setItem(ALERTS_KEY, "on");
    setDocumentVisibility("hidden");

    await expect(notifyCompletion(INPUT)).resolves.toBeUndefined();

    expect(constructed).toHaveLength(0);
  });
});

describe("requestAlerts", () => {
  it("pedido rejeitado pelo navegador vira bloqueio", async () => {
    installFakeNotification("default", "rejects");

    await expect(requestAlerts()).resolves.toBe("blocked");

    expect(window.localStorage.getItem(ALERTS_KEY)).toBeNull();
  });
});

describe("useCompletionAlert", () => {
  it("avisa uma vez por execução, sem nome de arquivo", async () => {
    const { constructed } = installFakeNotification("granted");
    installFakeServiceWorker("absent");
    window.localStorage.setItem(ALERTS_KEY, "on");
    setDocumentVisibility("hidden");
    const { rerender } = renderHook(
      ({ current }: { current: RunDetail }) => {
        useCompletionAlert(current);
      },
      { initialProps: { current: run("running") } },
    );

    rerender({ current: run("succeeded") });
    await waitFor(() => {
      expect(constructed).toHaveLength(1);
    });
    rerender({ current: run("succeeded") });
    // Espera a cadeia inteira de um possível segundo aviso (getRegistration e o construtor), não só
    // um microtask: a macrotarefa só roda depois de todas as promises pendentes.
    await act(async () => {
      await new Promise((resolve) => {
        setTimeout(resolve, 0);
      });
    });

    expect(constructed).toHaveLength(1);
    const [notification] = constructed;
    expect(notification?.options?.tag).toBe(RUN_ID);
    expect(notification?.options?.body).toBe("Image Forge · 3 arquivos");
    expect(notification?.options?.body).not.toContain(".png");
  });
});
