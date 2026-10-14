import { useEffect, useRef, type ReactElement, type ReactNode } from "react";

interface FocusHeadingProps {
  readonly children: ReactNode;
  /** Muda quando a etapa muda sem desmontar o título; cada valor novo devolve o foco a ele. */
  readonly focusKey?: string;
}

/**
 * Título da etapa (`h1`) que recebe o foco ao aparecer: numa SPA a troca de tela não move o foco
 * sozinha, e quem usa teclado ou leitor de tela ficaria no botão da etapa anterior.
 */
export function FocusHeading({ children, focusKey }: FocusHeadingProps): ReactElement {
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    heading.current?.focus();
  }, [focusKey]);
  return (
    <h1 ref={heading} tabIndex={-1}>
      {children}
    </h1>
  );
}
