import { ChevronDown } from "lucide-react";
import type { ReactElement } from "react";

import { TEXT } from "../text";
import { THEME_PREFERENCES } from "./theme";
import { useTheme } from "./useTheme";

const T = TEXT.theme;

/**
 * Escolha do tema no cabeçalho: três radios com label visível dentro de um `<details>`, sem
 * JavaScript de menu nem foco preso. O resumo mostra a escolha atual.
 */
export function ThemeSwitcher(): ReactElement {
  const { preference, choose } = useTheme();
  return (
    <details className="relative">
      <summary className="flex min-h-11 cursor-pointer items-center gap-2 rounded-lg px-3 text-sm text-fg-muted hover:bg-surface-sunken hover:text-fg">
        <span>{T.summary}</span>
        <span className="font-semibold text-fg">{T.options[preference]}</span>
        <ChevronDown aria-hidden="true" className="size-4" />
      </summary>
      <fieldset className="absolute right-0 z-10 mt-2 w-48 rounded-lg bg-surface-raised p-2 shadow-float">
        <legend className="sr-only">{T.legend}</legend>
        {THEME_PREFERENCES.map((option) => (
          <label
            key={option}
            className="flex min-h-11 cursor-pointer items-center gap-3 rounded-lg px-3 hover:bg-surface-sunken"
          >
            <input
              type="radio"
              name="tema"
              value={option}
              checked={preference === option}
              onChange={() => {
                choose(option);
              }}
            />
            {T.options[option]}
          </label>
        ))}
      </fieldset>
    </details>
  );
}
