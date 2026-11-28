import { PROBLEM_CODES, type ApiProblem } from "../api/problem";

/**
 * Parâmetro com que o download por navegação que falha volta à tela da execução: a API responde
 * 303 para `/execucoes/<uuid>?falha=<code>` (`download_navigation.py`).
 */
export const DOWNLOAD_FAILURE_PARAM = "falha";

/** O `code` da falha do download, só se for do catálogo; qualquer outro valor vira `null`. */
export function downloadFailureCode(search: URLSearchParams): ApiProblem["code"] | null {
  const value = search.get(DOWNLOAD_FAILURE_PARAM);
  return PROBLEM_CODES.find((code) => code === value) ?? null;
}
