import type { ReactElement, ReactNode } from "react";

interface EmptyStateProps {
  readonly title: string;
  readonly headingLevel?: 2 | 3;
  /** Espaço da ilustração; fica vazio até a decisão das ilustrações (D6, D7). */
  readonly illustration?: ReactNode;
  /** Explicação e, no máximo, uma ação. */
  readonly children?: ReactNode;
}

/** Estado vazio desenhado, no lugar de uma tela em branco (2.2). */
export function EmptyState({
  title,
  headingLevel = 2,
  illustration,
  children,
}: EmptyStateProps): ReactElement {
  const Heading = headingLevel === 2 ? "h2" : "h3";
  return (
    <div>
      {illustration}
      <Heading>{title}</Heading>
      {children}
    </div>
  );
}
