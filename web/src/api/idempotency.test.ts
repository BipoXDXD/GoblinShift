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

  function renderKeys() {
    return renderHook(({ intent }) => useIdempotencyKey(intent), {
      initialProps: { intent: "A" },
    });
  }

  /** Vai até a intenção e a envia, como o formulário faz antes do POST. */
  function send(hook: ReturnType<typeof renderKeys>, intent: string): string {
    hook.rerender({ intent });
    act(() => {
      hook.result.current.markSent(intent);
    });
    return hook.result.current.key;
  }

  it("A→B→A reaproveita a chave de A enviado", () => {
    const hook = renderKeys();
    const a = send(hook, "A");
    hook.rerender({ intent: "B" });
    const b = hook.result.current.key;

    hook.rerender({ intent: "A" });

    expect(b).not.toBe(a);
    expect(hook.result.current.key).toBe(a);
  });

  it("A→B→A sem enviar A dá chave nova a A", () => {
    const hook = renderKeys();
    const a = hook.result.current.key;
    hook.rerender({ intent: "B" });

    hook.rerender({ intent: "A" });

    expect(hook.result.current.key).not.toBe(a);
  });

  it("rotate dá chave nova a todos os comandos", () => {
    const hook = renderKeys();
    const b = send(hook, "B");
    const a = send(hook, "A");

    act(() => {
      hook.result.current.rotate();
    });
    const rotatedA = hook.result.current.key;
    hook.rerender({ intent: "B" });

    expect(rotatedA).not.toBe(a);
    expect(hook.result.current.key).not.toBe(b);
  });

  it("esquece o comando enviado mais antigo depois de 20 enviados", () => {
    const hook = renderKeys();
    const a = send(hook, "A");
    for (let other = 1; other <= 20; other += 1) {
      send(hook, `outra-${String(other)}`);
    }

    hook.rerender({ intent: "A" });

    expect(hook.result.current.key).not.toBe(a);
  });

  it("lembra o comando enviado enquanto há até 20 enviados no total", () => {
    const hook = renderKeys();
    const a = send(hook, "A");
    for (let other = 1; other <= 19; other += 1) {
      send(hook, `outra-${String(other)}`);
    }

    hook.rerender({ intent: "A" });

    expect(hook.result.current.key).toBe(a);
  });

  it("o comando enviado guarda a chave mesmo depois de 30 teclas em outro", () => {
    const { result, rerender } = renderHook(({ intent }) => useIdempotencyKey(intent), {
      initialProps: { intent: "A" },
    });
    const a = result.current.key;
    act(() => {
      result.current.markSent("A");
    });
    for (let typed = 1; typed <= 30; typed += 1) {
      rerender({ intent: `A${"x".repeat(typed)}` });
    }

    rerender({ intent: "A" });

    expect(result.current.key).toBe(a);
  });
});
