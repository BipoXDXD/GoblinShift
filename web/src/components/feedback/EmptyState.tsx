import type { ReactElement, ReactNode } from "react";

interface EmptyStateProps {
  readonly title: string;
  readonly headingLevel?: 2 | 3;
  /** A goblin do vazio (`GoblinIllustration`), onde a tela não tiver outra goblin. */
  readonly illustration?: ReactNode;
  /** Explicação e, no máximo, uma ação. */
  readonly children?: ReactNode;
}

/**
 * Estado vazio desenhado, no lugar de uma tela em branco (2.2). Centralizado porque o texto é
 * curto, de até três linhas (RUI cap. 4).
 */
export function EmptyState({
  title,
  headingLevel = 2,
  illustration,
  children,
}: EmptyStateProps): ReactElement {
  const Heading = headingLevel === 2 ? "h2" : "h3";
  return (
    <div className="flex flex-col items-center gap-2 rounded-lg bg-surface px-6 py-8 text-center text-fg-muted shadow-card">
      {illustration}
      <Heading className="text-lg font-semibold text-fg">{title}</Heading>
      {children}
    </div>
  );
}
