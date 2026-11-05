import { useMemo, type ReactElement } from "react";

import { FocusHeading } from "../components/a11y/FocusHeading";
import { LegalDocument } from "./LegalDocument";
import { parseMarkdown, type Block } from "./markdown";

interface LegalPageProps {
  /** Título da tela quando o texto não começa por um `#`. */
  readonly fallbackTitle: string;
  /** Conteúdo do arquivo de docs/legal/, importado no build (`?raw`). */
  readonly markdown: string;
}

/**
 * Termos de Uso e Aviso de Privacidade, lidos de docs/legal/: o repositório é a fonte única, e o
 * texto que a pessoa aceita é o mesmo que está versionado.
 */
export function LegalPage({ fallbackTitle, markdown }: LegalPageProps): ReactElement {
  const { title, blocks } = useMemo(() => splitTitle(parseMarkdown(markdown)), [markdown]);
  return (
    <article className="mx-auto flex w-full max-w-3xl flex-col gap-6">
      <FocusHeading>{title ?? fallbackTitle}</FocusHeading>
      <LegalDocument blocks={blocks} />
    </article>
  );
}

/** O primeiro `#` vira o `h1` da tela; o resto segue como no arquivo. */
function splitTitle(blocks: readonly Block[]): { title: string | null; blocks: readonly Block[] } {
  const [first, ...rest] = blocks;
  if (first?.kind !== "heading" || first.level !== 1) {
    return { title: null, blocks };
  }
  const title = first.inlines.map((inline) => (inline.kind === "text" ? inline.text : "")).join("");
  return { title, blocks: rest };
}
