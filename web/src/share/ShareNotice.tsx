import { TriangleAlert } from "lucide-react";
import type { ReactElement } from "react";

/**
 * Aviso do compartilhamento que não abriu (vencido, já usado, não chegou). Âmbar é a cor do aviso,
 * com o ícone e o texto juntos (a cor nunca é o único sinal). `role="alert"` por padrão: na
 * Bancada ele aparece depois de ler a caixa de entrada, com a tela já aberta. Na Oficina, que já
 * abre com ele, `status`.
 */
export function ShareNotice({
  children,
  role = "alert",
}: {
  readonly children: string;
  readonly role?: "alert" | "status";
}): ReactElement {
  return (
    <p
      role={role}
      className="flex max-w-prose gap-3 rounded-lg border-l-4 border-warning-accent bg-warning-bg p-4 text-warning-fg"
    >
      <TriangleAlert aria-hidden="true" className="mt-1 size-4 flex-none" />
      <span>{children}</span>
    </p>
  );
}
