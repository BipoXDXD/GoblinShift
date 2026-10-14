import { FileWarning } from "lucide-react";
import { useMemo, type ReactElement } from "react";

import { FocusHeading } from "../components/a11y/FocusHeading";
import { TEXT } from "../text";
import { LegalDocument } from "./LegalDocument";
import { parseMarkdown, type Block } from "./markdown";

const T = TEXT.legal;

interface LegalPageProps {
  /** Título da tela quando o texto não começa por um `#`. */
  readonly fallbackTitle: string;
  /** Conteúdo do arquivo de docs/legal/, importado no build (`?raw`). */
  readonly markdown: string;
}

/**
 * Termos de Uso e Aviso de Privacidade, lidos de docs/legal/: o repositório é a fonte única, e o
 * texto que a pessoa aceita é o mesmo que está versionado. Enquanto não houver revisão jurídica,
 * o aviso de rascunho vem antes de tudo.
 */
export function LegalPage({ fallbackTitle, markdown }: LegalPageProps): ReactElement {
  const { title, blocks } = useMemo(() => splitTitle(parseMarkdown(markdown)), [markdown]);
  return (
    <article className="mx-auto flex w-full max-w-3xl flex-col gap-6">
      <FocusHeading>{title ?? fallbackTitle}</FocusHeading>
      <aside
        aria-label={T.draftTitle}
        className="flex max-w-prose gap-3 rounded-lg border-l-4 border-warning-fg bg-warning-bg p-4 text-warning-fg"
      >
        <FileWarning aria-hidden="true" className="mt-1 size-4 flex-none" />
        <p>
          <strong className="font-semibold">{T.draftTitle}.</strong> {T.draftBody}
        </p>
      </aside>
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
