import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useNow } from "./useNow";

const START = Date.parse("2026-10-14T12:00:00Z");
const MINUTE = 60_000;

describe("useNow", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("começa no relógio e anda a cada intervalo", () => {
    let clock = START;
    const { result } = renderHook(() => useNow(() => clock, MINUTE));
    expect(result.current).toBe(START);

    clock = START + 3 * MINUTE;
    act(() => {
      vi.advanceTimersByTime(MINUTE);
    });

    expect(result.current).toBe(START + 3 * MINUTE);
  });

  it("antes do intervalo, não relê o relógio", () => {
    let clock = START;
    const { result } = renderHook(() => useNow(() => clock, MINUTE));

    clock = START + MINUTE;
    act(() => {
      vi.advanceTimersByTime(MINUTE - 1);
    });

    expect(result.current).toBe(START);
  });
});
