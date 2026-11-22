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

  it("A→B→A reaproveita a chave de A", () => {
    const { result, rerender } = renderHook(({ intent }) => useIdempotencyKey(intent), {
      initialProps: { intent: "A" },
    });
    const a = result.current.key;
    rerender({ intent: "B" });
    const b = result.current.key;

    rerender({ intent: "A" });

    expect(b).not.toBe(a);
    expect(result.current.key).toBe(a);
  });

  it("rotate dá chave nova a todos os comandos", () => {
    const { result, rerender } = renderHook(({ intent }) => useIdempotencyKey(intent), {
      initialProps: { intent: "A" },
    });
    rerender({ intent: "B" });
    const b = result.current.key;
    rerender({ intent: "A" });
    const a = result.current.key;

    act(() => {
      result.current.rotate();
    });
    const rotatedA = result.current.key;
    rerender({ intent: "B" });

    expect(rotatedA).not.toBe(a);
    expect(result.current.key).not.toBe(b);
  });

  it("esquece a intenção mais antiga depois de 20", () => {
    const { result, rerender } = renderHook(({ intent }) => useIdempotencyKey(intent), {
      initialProps: { intent: "A" },
    });
    const a = result.current.key;
    for (let other = 1; other <= 20; other += 1) {
      rerender({ intent: `outra-${String(other)}` });
    }

    rerender({ intent: "A" });

    expect(result.current.key).not.toBe(a);
  });

  it("lembra a intenção enquanto há até 20 no total", () => {
    const { result, rerender } = renderHook(({ intent }) => useIdempotencyKey(intent), {
      initialProps: { intent: "A" },
    });
    const a = result.current.key;
    for (let other = 1; other <= 19; other += 1) {
      rerender({ intent: `outra-${String(other)}` });
    }

    rerender({ intent: "A" });

    expect(result.current.key).toBe(a);
  });
});
