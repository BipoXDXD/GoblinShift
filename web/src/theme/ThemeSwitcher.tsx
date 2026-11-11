import { ChevronDown } from "lucide-react";
import { useEffect, useRef, type ReactElement } from "react";

import { TEXT } from "../text";
import { THEME_PREFERENCES } from "./theme";
import { useTheme } from "./useTheme";

const T = TEXT.theme;

/**
 * Escolha do tema no cabeçalho: três radios com label visível dentro de um `<details>`, sem foco
 * preso. O resumo mostra a escolha atual. O painel abre por cima do conteúdo, então fecha como um
 * menu suspenso: com Esc (o foco volta ao resumo), quando o foco sai dele e com um toque ou clique
 * fora, para nunca cobrir o elemento focado (WCAG 2.4.11).
 */
export function ThemeSwitcher(): ReactElement {
  const { preference, choose } = useTheme();
  const details = useRef<HTMLDetailsElement>(null);
  // Ouvintes nativos: os eventos sobem dos controles de dentro (resumo e radios), e o <details>
  // só os recebe, sem virar ele mesmo um controle.
  useEffect(() => {
    const element = details.current;
    if (element === null) {
      return undefined;
    }
    // Toque ou clique fora: no WebKit, tocar num botão ou link não dá foco, então o focusout
    // sozinho não fecharia. Não move o foco: quem tocou decide para onde ele vai.
    const closeOnPointerOutside = (event: Event): void => {
      if (element.open && event.target instanceof Node && !element.contains(event.target)) {
        element.open = false;
      }
    };
    const page = element.ownerDocument;
    element.addEventListener("keydown", closeOnEscape);
    element.addEventListener("focusout", closeWhenFocusLeaves);
    page.addEventListener("pointerdown", closeOnPointerOutside);
    return () => {
      element.removeEventListener("keydown", closeOnEscape);
      element.removeEventListener("focusout", closeWhenFocusLeaves);
      page.removeEventListener("pointerdown", closeOnPointerOutside);
    };
  }, []);
  return (
    <details ref={details} className="relative">
      <summary className="flex min-h-11 cursor-pointer items-center gap-2 rounded-lg px-3 text-sm text-fg-muted hover:bg-surface-sunken hover:text-fg">
        <span>{T.summary}</span>
        <span className="font-semibold text-fg">{T.options[preference]}</span>
        <ChevronDown aria-hidden="true" className="size-4" />
      </summary>
      <fieldset className="absolute right-0 z-(--z-menu) mt-2 w-48 rounded-lg bg-surface-raised p-2 shadow-float">
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

function closeOnEscape(event: KeyboardEvent): void {
  const details = event.currentTarget;
  if (event.key !== "Escape" || !(details instanceof HTMLDetailsElement) || !details.open) {
    return;
  }
  event.preventDefault();
  details.open = false;
  details.querySelector("summary")?.focus();
}

/**
 * Fecha só quando o foco vai para outro elemento fora do seletor. Sem destino (`relatedTarget`
 * nulo), como no clique num label, que não é focável, fica aberto: fechar ali esconderia a opção
 * antes de o clique chegar nela.
 */
function closeWhenFocusLeaves(event: FocusEvent): void {
  const details = event.currentTarget;
  const next = event.relatedTarget;
  if (details instanceof HTMLDetailsElement && next instanceof Node && !details.contains(next)) {
    details.open = false;
  }
}
