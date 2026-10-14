import { generatePath } from "react-router";

import type { RecipeId } from "../recipes/types";

// Todas as rotas da interface saem daqui: o idioma das URLs (D12) ainda não foi decidido, e trocá-lo
// é mudar só estes textos. Os segmentos atuais são provisórios.
export const PATHS = {
  home: "/",
  workbench: "/bancada/:recipeId",
  run: "/execucoes/:runId",
} as const;

export function workbenchPath(recipeId: RecipeId): string {
  return generatePath(PATHS.workbench, { recipeId });
}

export function runPath(runId: string): string {
  return generatePath(PATHS.run, { runId });
}
