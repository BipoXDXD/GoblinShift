import { useEffect, useState } from "react";

import {
  DARK_QUERY,
  readPreference,
  resolveTheme,
  writePreference,
  type ThemePreference,
} from "./theme";

interface ThemeControl {
  readonly preference: ThemePreference;
  readonly choose: (preference: ThemePreference) => void;
}

/**
 * Preferência de tema da pessoa, aplicada como `data-theme` no `<html>` (o mesmo atributo que o
 * `theme-init.js` grava na carga). Em `system`, acompanha a troca de tema do sistema operacional.
 */
export function useTheme(): ThemeControl {
  const [preference, setPreference] = useState(() => readPreference(window.localStorage));

  useEffect(() => {
    const system = window.matchMedia(DARK_QUERY);
    const apply = (): void => {
      document.documentElement.dataset["theme"] = resolveTheme(preference, system.matches);
    };
    apply();
    if (preference !== "system") {
      return undefined;
    }
    system.addEventListener("change", apply);
    return () => {
      system.removeEventListener("change", apply);
    };
  }, [preference]);

  const choose = (next: ThemePreference): void => {
    writePreference(window.localStorage, next);
    setPreference(next);
  };
  return { preference, choose };
}
