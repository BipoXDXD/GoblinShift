import type { ReactElement, ReactNode } from "react";

import { Heading, type HeadingLevel } from "../a11y/Heading";

interface EmptyStateProps {
  readonly title: string;
  readonly headingLevel?: HeadingLevel;
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
  return (
    <div className="flex flex-col items-center gap-2 rounded-lg bg-surface px-6 py-8 text-center text-fg-muted shadow-card">
      {illustration}
      <Heading level={headingLevel} className="text-lg font-semibold text-fg">
        {title}
      </Heading>
      {children}
    </div>
  );
}
