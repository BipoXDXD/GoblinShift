import { CircleAlert } from "lucide-react";
import type { ReactElement, ReactNode } from "react";

/**
 * Moldura do alerta de falha, em `role="alert"`: faixa e fundo de perigo com o ícone ao lado. O
 * texto fica com quem a usa (ProblemAlert, DownloadFailureNotice).
 */
export function DangerAlert({ children }: { readonly children: ReactNode }): ReactElement {
  return (
    <div
      role="alert"
      className="flex gap-3 rounded-lg border-l-4 border-danger-accent bg-danger-bg p-4 text-danger-fg"
    >
      <CircleAlert aria-hidden="true" className="mt-1 size-4 flex-none" />
      {children}
    </div>
  );
}
