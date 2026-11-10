import type { ReactElement } from "react";

/**
 * Espera entre a região vazia e o texto. Alguns leitores (VoiceOver no Safari, NVDA no Firefox) perdem
 * o texto que chega logo depois de a região montar, e uma mudança de foco cancela a fala pendente:
 * quem anuncia junto de um desses eventos põe o texto depois desta espera.
 */
export const ANNOUNCE_DELAY_MS = 150;

interface LiveRegionProps {
  /** Texto a anunciar; `null` deixa a região vazia, mas presente. */
  readonly message: string | null;
  /** `assertive` interrompe a leitura: só para falhas que pedem ação. */
  readonly politeness?: "polite" | "assertive";
  /** Esconde o texto da tela quando ele repete o que já está visível. */
  readonly visuallyHidden?: boolean;
}

/**
 * Região `aria-live` (WCAG 4.1.3). Fica sempre montada, porque o leitor de tela só anuncia mudanças
 * numa região que já conhecia; quem a usa troca só o texto, e só quando o estado muda.
 */
export function LiveRegion({
  message,
  politeness = "polite",
  visuallyHidden = false,
}: LiveRegionProps): ReactElement {
  return (
    <div
      role={politeness === "assertive" ? "alert" : "status"}
      aria-live={politeness}
      aria-atomic="true"
      {...(visuallyHidden ? { className: "sr-only" } : {})}
    >
      {message}
    </div>
  );
}
