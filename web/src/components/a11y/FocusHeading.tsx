import { useEffect, useRef, type ReactElement, type ReactNode } from "react";

interface FocusHeadingProps {
  readonly children: ReactNode;
  /** Muda quando a etapa muda sem desmontar o título; cada valor novo devolve o foco a ele. */
  readonly focusKey?: string;
  /** `hero` só na abertura da Oficina; as telas de trabalho usam `page`. */
  readonly size?: "page" | "hero";
  /** `false` quando outro alvo da tela leva o foco ao abrir (a seção de um link com âncora). */
  readonly takesFocus?: boolean;
}

// Títulos encolhem mais que o corpo no celular (RUI cap. 3); tracking-tight só de 30 px para cima.
const SIZES = {
  page: "text-2xl md:text-3xl md:tracking-tight",
  hero: "text-3xl tracking-tight md:text-4xl",
} as const;

/**
 * Título da etapa (`h1`) que recebe o foco ao aparecer: numa SPA a troca de tela não move o foco
 * sozinha, e quem usa teclado ou leitor de tela ficaria no botão da etapa anterior.
 */
export function FocusHeading({
  children,
  focusKey,
  size = "page",
  takesFocus = true,
}: FocusHeadingProps): ReactElement {
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    if (takesFocus) {
      heading.current?.focus();
    }
  }, [focusKey, takesFocus]);
  return (
    <h1
      ref={heading}
      tabIndex={-1}
      className={`font-display font-semibold text-balance text-fg ${SIZES[size]}`}
    >
      {children}
    </h1>
  );
}
