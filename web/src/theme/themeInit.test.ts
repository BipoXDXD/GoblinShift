import { describe, expect, it } from "vitest";

import script from "../../public/theme-init.js?raw";

// O script do <head> roda antes do React e precisa decidir igual a resolveTheme e parsePreference.
// Roda com localStorage, matchMedia e document próprios, no lugar dos globais da página.
// eslint-disable-next-line @typescript-eslint/no-implied-eval -- executa o arquivo publicado, sem cópia
const themeInit = new Function("localStorage", "matchMedia", "document", script);

function themeAfterInit(storage: Pick<Storage, "getItem">, systemPrefersDark: boolean): string {
  const dataset: Record<string, string> = {};
  const matchMedia = (query: string) => ({
    matches: query === "(prefers-color-scheme: dark)" && systemPrefersDark,
  });
  Reflect.apply(themeInit, undefined, [storage, matchMedia, { documentElement: { dataset } }]);
  return dataset["theme"] ?? "nenhum";
}

function storageWith(stored: string | null): Pick<Storage, "getItem"> {
  return { getItem: (key) => (key === "gs-theme" ? stored : null) };
}

describe("theme-init.js", () => {
  it.each([
    [null, false, "light"],
    [null, true, "dark"],
    ["system", false, "light"],
    ["system", true, "dark"],
    ["light", true, "light"],
    ["dark", false, "dark"],
    ["sepia", true, "dark"],
  ] as const)("gravado %j, sistema escuro = %s → %s", (stored, systemPrefersDark, expected) => {
    expect(themeAfterInit(storageWith(stored), systemPrefersDark)).toBe(expected);
  });

  it.each([
    [false, "light"],
    [true, "dark"],
  ] as const)(
    "armazenamento bloqueado, sistema escuro = %s → %s",
    (systemPrefersDark, expected) => {
      const blocked = {
        getItem: (): never => {
          throw new DOMException("bloqueado", "SecurityError");
        },
      };
      expect(themeAfterInit(blocked, systemPrefersDark)).toBe(expected);
    },
  );
});
