import { cleanup, render } from "@testing-library/react";
import { useEffect } from "react";
import { describe, expect, it, vi } from "vitest";

import { allowConsole, consoleCalls, startConsoleGuard, stopConsoleGuard } from "./consoleGuard";

// O setup.ts já ligou o guard antes de cada teste. Cada caso lê as violações com stopConsoleGuard e
// religa com startConsoleGuard, para o afterEach do setup encontrar o guard limpo.

// O React avisa da falta de key uma vez por tipo do pai (aqui, a tag da lista) em cada arquivo:
// por isso o primeiro caso usa <ul> e o da liberação usa <ol>.
function UnkeyedList() {
  return (
    <ul>
      {["a", "b"].map((item) => (
        <li>{item}</li>
      ))}
    </ul>
  );
}

function UnkeyedOrderedList() {
  return (
    <ol>
      {["a", "b"].map((item) => (
        <li>{item}</li>
      ))}
    </ol>
  );
}

function WarnsOnUnmount() {
  useEffect(
    () => () => {
      console.error("saindo");
    },
    [],
  );
  return null;
}

describe("guard de console", () => {
  it("lista sem key vira violação de console.error", () => {
    render(<UnkeyedList />);
    const violations = stopConsoleGuard();
    startConsoleGuard();
    expect(violations).toHaveLength(1);
    expect(violations[0]).toMatch(/^console\.error sem liberação: .*unique "key" prop/);
  });

  it("com a liberação, a lista sem key passa e a chamada fica em consoleCalls", () => {
    allowConsole("error", /unique "key" prop/, "prova do guard");
    render(<UnkeyedOrderedList />);
    expect(consoleCalls().map((call) => call.method)).toEqual(["error"]);
    expect(stopConsoleGuard()).toEqual([]);
    startConsoleGuard();
  });

  it("liberação que nunca casa é violação", () => {
    allowConsole("warn", /nunca aparece/, "prova do guard");
    expect(stopConsoleGuard()).toEqual([
      "liberação sem uso: console.warn /nunca aparece/ (prova do guard)",
    ]);
    startConsoleGuard();
  });

  it("console.warn também é pego; log não é violação mas é gravado", () => {
    console.warn("cuidado %s", "agora");
    console.log("só informação");
    expect(consoleCalls()).toEqual([
      { method: "warn", args: ["cuidado %s", "agora"] },
      { method: "log", args: ["só informação"] },
    ]);
    expect(stopConsoleGuard()).toEqual(["console.warn sem liberação: cuidado agora"]);
    startConsoleGuard();
  });

  it("aviso na desmontagem conta para o teste", () => {
    render(<WarnsOnUnmount />);
    cleanup();
    expect(stopConsoleGuard()).toEqual(["console.error sem liberação: saindo"]);
    startConsoleGuard();
  });

  it("motivo vazio lança", () => {
    expect(() => {
      allowConsole("error", /x/, "  ");
    }).toThrow(/motivo/);
  });

  it("regex sticky (flag y) é recusada", () => {
    expect(() => {
      allowConsole("error", /x/y, "prova do guard");
    }).toThrow(/sticky/);
  });

  it("Error e objeto entram na mensagem montada", () => {
    allowConsole("error", /Falha: TypeError: quebrou \{"id":1\}/, "prova do guard");
    console.error("Falha:", new TypeError("quebrou"), { id: 1 });
    expect(stopConsoleGuard()).toEqual([]);
    startConsoleGuard();
  });

  it("objeto circular sem protótipo entra na mensagem sem derrubar o guard", () => {
    const bare: Record<string, unknown> = Object.create(null) as Record<string, unknown>;
    bare["self"] = bare;
    console.error("circular:", bare);
    expect(stopConsoleGuard()).toEqual(["console.error sem liberação: circular: [object Object]"]);
    startConsoleGuard();
  });

  it("DOMException entra com nome e mensagem, como Error", () => {
    console.error(new DOMException("operação abortada", "AbortError"));
    expect(stopConsoleGuard()).toEqual([
      "console.error sem liberação: AbortError: operação abortada",
    ]);
    startConsoleGuard();
  });

  it("%% vira % e não consome argumento, como no util.format", () => {
    console.warn("100%% de %s", "uso");
    expect(stopConsoleGuard()).toEqual(["console.warn sem liberação: 100% de uso"]);
    startConsoleGuard();
  });

  it("espião do Vitest restaurado não tira o guard do próximo start", () => {
    // eslint-disable-next-line no-restricted-syntax -- o espião direto é o cenário sob teste aqui
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    vi.restoreAllMocks();
    stopConsoleGuard();
    startConsoleGuard();
    console.error("depois do espião");
    expect(consoleCalls()).toEqual([{ method: "error", args: ["depois do espião"] }]);
    stopConsoleGuard();
    startConsoleGuard();
  });

  it("start com o guard já ativo avisa e recomeça do zero", () => {
    console.log("gravado");
    const warnings: string[] = [];
    startConsoleGuard((message) => {
      warnings.push(message);
    });
    expect(warnings).toHaveLength(1);
    expect(warnings[0]).toMatch(/guard de console já estava ativo/);
    expect(consoleCalls()).toEqual([]);
  });

  it("start zera o que foi gravado antes", () => {
    console.error("antes");
    stopConsoleGuard();
    startConsoleGuard();
    expect(consoleCalls()).toEqual([]);
  });
});
