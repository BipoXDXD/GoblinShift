import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { RunStatus } from "./api";
import { signaledStatus, useCompletionTitle } from "./completionSignal";

const ROUTE_TITLE = "Execução · GoblinShift";

function setVisibility(state: DocumentVisibilityState): void {
  Object.defineProperty(document, "visibilityState", { configurable: true, value: state });
  document.dispatchEvent(new Event("visibilitychange"));
}

describe("signaledStatus", () => {
  it.each<[RunStatus | null, RunStatus, ReturnType<typeof signaledStatus>]>([
    ["running", "succeeded", "succeeded"],
    ["queued", "succeeded_with_warnings", "succeeded_with_warnings"],
    ["stopping", "failed", "failed"],
    [null, "succeeded", null],
    ["running", "canceled", null],
    ["succeeded", "succeeded", null],
    ["queued", "running", null],
  ])("%s → %s dá %s", (previous, next, expected) => {
    expect(signaledStatus(previous, next)).toBe(expected);
  });
});

describe("useCompletionTitle", () => {
  beforeEach(() => {
    document.title = ROUTE_TITLE;
  });

  afterEach(() => {
    // Volta ao padrão do jsdom: o getter do protótipo, que diz "visible".
    Reflect.deleteProperty(document, "visibilityState");
  });

  it("com a aba escondida o título ganha ✓ Concluída e volta ao ficar visível", () => {
    setVisibility("hidden");
    const { result, rerender } = renderHook(
      ({ status }: { status: RunStatus | undefined }) => useCompletionTitle(status),
      { initialProps: { status: "running" as RunStatus | undefined } },
    );
    expect(document.title).toBe(ROUTE_TITLE);

    rerender({ status: "succeeded" });

    expect(result.current).toBe("succeeded");
    expect(document.title).toBe("✓ Concluída · Execução · GoblinShift");
    act(() => {
      setVisibility("visible");
    });
    expect(document.title).toBe(ROUTE_TITLE);
  });

  it("com a aba visível o título não muda", () => {
    const { result, rerender } = renderHook(
      ({ status }: { status: RunStatus | undefined }) => useCompletionTitle(status),
      { initialProps: { status: "running" as RunStatus | undefined } },
    );

    rerender({ status: "failed" });

    expect(result.current).toBe("failed");
    expect(document.title).toBe(ROUTE_TITLE);
  });

  it("a execução que já abre terminada não muda o título", () => {
    setVisibility("hidden");
    const { result } = renderHook(() => useCompletionTitle("succeeded"));

    expect(result.current).toBeNull();
    expect(document.title).toBe(ROUTE_TITLE);
  });
});
