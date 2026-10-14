/**
 * Preferência de tema (D4): seguir o sistema, ou claro e escuro fixos. A mesma regra roda antes do
 * React em `public/theme-init.js`, para a primeira pintura já sair no tema certo.
 */
export type ThemePreference = "system" | "light" | "dark";
export type ResolvedTheme = "light" | "dark";

export const THEME_PREFERENCES: readonly ThemePreference[] = ["system", "light", "dark"];

/** Chave no `localStorage`; o `theme-init.js` lê a mesma. */
const STORAGE_KEY = "gs-theme";
export const DARK_QUERY = "(prefers-color-scheme: dark)";

export function resolveTheme(
  preference: ThemePreference,
  systemPrefersDark: boolean,
): ResolvedTheme {
  if (preference === "system") {
    return systemPrefersDark ? "dark" : "light";
  }
  return preference;
}

/** O `localStorage` é entrada externa: qualquer valor desconhecido vira `system`. */
export function parsePreference(value: string | null): ThemePreference {
  return THEME_PREFERENCES.find((preference) => preference === value) ?? "system";
}

type PreferenceStorage = Pick<Storage, "getItem" | "setItem">;

export function readPreference(storage: PreferenceStorage): ThemePreference {
  try {
    return parsePreference(storage.getItem(STORAGE_KEY));
  } catch {
    // Armazenamento bloqueado (modo privado, política do navegador): segue o sistema.
    return "system";
  }
}

export function writePreference(storage: PreferenceStorage, preference: ThemePreference): void {
  try {
    storage.setItem(STORAGE_KEY, preference);
  } catch {
    // Sem armazenamento a escolha vale só nesta página; o tema já foi aplicado por quem chamou.
  }
}
