import type { ReactElement, ReactNode } from "react";

interface CardProps {
  readonly as?: "div" | "section" | "article";
  readonly "aria-labelledby"?: string;
  readonly children: ReactNode;
}

/**
 * Superfície de conteúdo: sombra de nível 2 no claro e superfície mais clara no escuro, sem borda
 * (RUI cap. 8, "Use fewer borders"); 24 px de padding no celular e 32 no desktop.
 */
export function Card({ as: Element = "div", children, ...props }: CardProps): ReactElement {
  return (
    <Element {...props} className="rounded-lg bg-surface p-6 shadow-2 md:p-8">
      {children}
    </Element>
  );
}
