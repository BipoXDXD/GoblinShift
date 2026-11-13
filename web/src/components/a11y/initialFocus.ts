/**
 * Marca o elemento da moldura que tomou o foco inicial antes da tela (o alerta de login recusado).
 * O `h1` da tela, que costuma chegar depois num chunk lazy, não o tira de lá: a mensagem seria
 * pulada pela leitura que parte do título (WCAG 4.1.3 e 2.4.3).
 */
export const HOLDS_INITIAL_FOCUS = "data-holds-initial-focus";

/** O foco está num elemento marcado com {@link HOLDS_INITIAL_FOCUS}. */
export function isInitialFocusHeld(): boolean {
  const active = document.activeElement;
  return active !== null && active.closest(`[${HOLDS_INITIAL_FOCUS}]`) !== null;
}
