import { describe, expect, it } from "vitest";

import { parsePreference, readPreference, resolveTheme, writePreference } from "./theme";

describe("resolveTheme", () => {
  it.each([
    ["system", false, "light"],
    ["system", true, "dark"],
    ["light", false, "light"],
    ["light", true, "light"],
    ["dark", false, "dark"],
    ["dark", true, "dark"],
  ] as const)("%s com sistema escuro = %s → %s", (preference, systemPrefersDark, expected) => {
    expect(resolveTheme(preference, systemPrefersDark)).toBe(expected);
  });
});

describe("parsePreference", () => {
  it.each(["system", "light", "dark"] as const)("aceita %s", (value) => {
    expect(parsePreference(value)).toBe(value);
  });

  it.each([null, "", "Dark", "sepia", " dark"])("valor desconhecido %j vira system", (value) => {
    expect(parsePreference(value)).toBe("system");
  });
});

describe("preferência gravada", () => {
  it("sem nada gravado, segue o sistema", () => {
    expect(readPreference(window.localStorage)).toBe("system");
  });

  it("grava e lê a mesma escolha", () => {
    writePreference(window.localStorage, "dark");

    expect(window.localStorage.getItem("gs-theme")).toBe("dark");
    expect(readPreference(window.localStorage)).toBe("dark");
  });

  it("armazenamento bloqueado na leitura segue o sistema", () => {
    expect(readPreference(blockedStorage())).toBe("system");
  });

  it("armazenamento bloqueado na gravação não derruba a troca de tema", () => {
    expect(() => {
      writePreference(blockedStorage(), "light");
    }).not.toThrow();
  });
});

function blockedStorage(): Pick<Storage, "getItem" | "setItem"> {
  const blocked = (): never => {
    throw new DOMException("bloqueado", "SecurityError");
  };
  return { getItem: blocked, setItem: blocked };
}
