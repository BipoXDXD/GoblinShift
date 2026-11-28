import { generatePath } from "react-router";

import type { RecipeId } from "../recipes/types";
import { LOCAL_PATH } from "./localPath";

// Todas as rotas da interface saem daqui: o idioma das URLs (D12) ainda não foi decidido, e trocá-lo
// é mudar só estes textos. Os segmentos atuais são provisórios.
export const PATHS = {
  home: "/",
  workbench: "/bancada/:recipeId",
  run: "/execucoes/:runId",
  history: "/historico",
  signIn: "/entrar",
  access: "/acesso",
  apiGuide: "/acesso/guia-da-api",
  terms: "/termos",
  privacy: "/privacidade",
  credits: "/creditos",
  acceptTerms: "/termos/aceitar",
  accountDeleted: "/conta-excluida",
  // Destino do `share_target` do manifest (ADR 064): o service worker recebe o POST e manda o GET.
  share: "/compartilhar",
} as const;

/**
 * O caminho sem a barra final (`/termos/` → `/termos`), ou `null` quando já está sem ela ou quando
 * o resultado não seria um caminho deste site. O router abre `/termos/` na rota `/termos`, mas o
 * `pathname` guarda a barra, e toda comparação exata com `PATHS` erraria.
 */
export function withoutTrailingSlash(pathname: string): string | null {
  if (!pathname.endsWith("/") || !LOCAL_PATH.test(pathname)) {
    return null;
  }
  let end = pathname.length;
  while (end > 1 && pathname.charAt(end - 1) === "/") {
    end -= 1;
  }
  return end === pathname.length ? null : pathname.slice(0, end);
}

/** Parâmetro com o caminho local para onde voltar (Entrar e aceite dos termos). */
export const RETURN_PARAM = "volta";

export function workbenchPath(recipeId: RecipeId): string {
  return generatePath(PATHS.workbench, { recipeId });
}

/** Parâmetro da Bancada com o id do preset que pré-preenche os parâmetros. */
export const PRESET_PARAM = "preset";

export function workbenchPresetPath(recipeId: RecipeId, presetId: string): string {
  const query = new URLSearchParams({ [PRESET_PARAM]: presetId }).toString();
  return `${workbenchPath(recipeId)}?${query}`;
}

/** Parâmetro da Bancada que pede o arquivo de exemplo da receita (primeiro uso, D4). */
export const SAMPLE_PARAM = "exemplo";

/** Bancada que abre já com o arquivo de exemplo; a página tira o parâmetro depois de carregar. */
export function workbenchSamplePath(recipeId: RecipeId): string {
  const query = new URLSearchParams({ [SAMPLE_PARAM]: "1" }).toString();
  return `${workbenchPath(recipeId)}?${query}`;
}

/** Parâmetro da Bancada com o id da execução cuja configuração se reusa (Histórico). */
export const RUN_PARAM = "execucao";

export function workbenchFromRunPath(recipeId: RecipeId, runId: string): string {
  const query = new URLSearchParams({ [RUN_PARAM]: runId }).toString();
  return `${workbenchPath(recipeId)}?${query}`;
}

/** Parâmetro da Bancada com o id do compartilhamento cujos arquivos ela recebe. */
export const SHARED_PARAM = "compartilhado";

export function workbenchSharedPath(recipeId: RecipeId, shareId: string): string {
  const query = new URLSearchParams({ [SHARED_PARAM]: shareId }).toString();
  return `${workbenchPath(recipeId)}?${query}`;
}

/** Parâmetro da página do compartilhamento com o id que o service worker deu (sw.ts). */
export const SHARE_ID_PARAM = "id";

/**
 * Parâmetro da Oficina quando o compartilhamento não chegou (sem service worker, o Nginx manda
 * `/?compartilhamento=indisponivel`; ver docker/nginx e sw.ts).
 */
export const SHARE_STATUS_PARAM = "compartilhamento";
export const SHARE_UNAVAILABLE = "indisponivel";

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
