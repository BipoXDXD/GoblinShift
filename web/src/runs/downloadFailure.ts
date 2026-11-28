import { PROBLEM_CODES, type ApiProblem } from "../api/problem";

/**
 * Parâmetro com que o download por navegação que falha volta à tela da execução: a API responde
 * 303 para `/execucoes/<uuid>?falha=<code>` (`download_navigation.py`).
 */
export const DOWNLOAD_FAILURE_PARAM = "falha";

/** Código da recusa com que o download por navegação volta: um `code` do catálogo. */
export type DownloadFailureCode = ApiProblem["code"];

/** O `code` da falha do download, só se for do catálogo; qualquer outro valor vira `null`. */
export function downloadFailureCode(search: URLSearchParams): DownloadFailureCode | null {
  const value = search.get(DOWNLOAD_FAILURE_PARAM);
  return PROBLEM_CODES.find((code) => code === value) ?? null;
}
