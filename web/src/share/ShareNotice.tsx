import { TriangleAlert } from "lucide-react";
import type { ReactElement } from "react";

/**
 * Aviso do compartilhamento que não abriu (vencido, já usado, não chegou). Âmbar é a cor do aviso,
 * com o ícone e o texto juntos (a cor nunca é o único sinal). `role="alert"`: na Bancada ele
 * aparece depois de ler a caixa de entrada, com a tela já aberta.
 */
export function ShareNotice({ children }: { readonly children: string }): ReactElement {
  return (
    <p
      role="alert"
      className="flex max-w-prose gap-3 rounded-lg border-l-4 border-warning-accent bg-warning-bg p-4 text-warning-fg"
    >
      <TriangleAlert aria-hidden="true" className="mt-1 size-4 flex-none" />
      <span>{children}</span>
    </p>
  );
}
