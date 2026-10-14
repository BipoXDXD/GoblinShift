import { generatePath } from "react-router";

import type { RecipeId } from "../recipes/types";

// Todas as rotas da interface saem daqui: o idioma das URLs (D12) ainda não foi decidido, e trocá-lo
// é mudar só estes textos. Os segmentos atuais são provisórios.
export const PATHS = {
  home: "/",
  workbench: "/bancada/:recipeId",
  run: "/execucoes/:runId",
  history: "/historico",
  signIn: "/entrar",
  access: "/acesso",
  terms: "/termos",
  privacy: "/privacidade",
  acceptTerms: "/termos/aceitar",
  accountDeleted: "/conta-excluida",
} as const;

/** Parâmetro com o caminho local para onde voltar (Entrar e aceite dos termos). */
export const RETURN_PARAM = "volta";

export function workbenchPath(recipeId: RecipeId): string {
  return generatePath(PATHS.workbench, { recipeId });
}

export function runPath(runId: string): string {
  return generatePath(PATHS.run, { runId });
}

/** Tela do aceite dos termos, que volta para `returnTo` (já conferido por `safeReturnTo`). */
export function acceptTermsPath(returnTo: string): string {
  return withReturn(PATHS.acceptTerms, returnTo);
}

/** Tela Entrar, que volta para `returnTo` (já conferido por `safeReturnTo`). */
export function signInPath(returnTo: string): string {
  return withReturn(PATHS.signIn, returnTo);
}

function withReturn(path: string, returnTo: string): string {
  return returnTo === PATHS.home
    ? path
    : `${path}?${new URLSearchParams({ [RETURN_PARAM]: returnTo }).toString()}`;
}
