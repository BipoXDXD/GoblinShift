import { act, renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { useIdempotencyKey } from "./idempotency";

const KEY_FORMAT = /^[A-Za-z0-9_-]{16,64}$/;

describe("useIdempotencyKey", () => {
  it("gera uma chave no formato aceito pela API", () => {
    const { result } = renderHook(() => useIdempotencyKey("intencao"));

    expect(result.current.key).toMatch(KEY_FORMAT);
  });

  it("mantém a chave enquanto a intenção não muda (retentativa)", () => {
    const { result, rerender } = renderHook(({ intent }) => useIdempotencyKey(intent), {
      initialProps: { intent: "a" },
    });
    const first = result.current.key;

    rerender({ intent: "a" });

    expect(result.current.key).toBe(first);
  });

  it("troca a chave quando a intenção muda", () => {
    const { result, rerender } = renderHook(({ intent }) => useIdempotencyKey(intent), {
      initialProps: { intent: "a" },
    });
    const first = result.current.key;

    rerender({ intent: "b" });

    expect(result.current.key).not.toBe(first);
  });

  it("gera chave nova depois de rotate (intenção concluída)", () => {
    const { result } = renderHook(() => useIdempotencyKey("a"));
    const first = result.current.key;

    act(() => {
      result.current.rotate();
    });

    expect(result.current.key).not.toBe(first);
    expect(result.current.key).toMatch(KEY_FORMAT);
  });
});
