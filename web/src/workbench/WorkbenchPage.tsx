import type { ReactElement } from "react";
import { useParams } from "react-router";

import { NotFoundPage } from "../app/NotFoundPage";
import { findRecipe } from "../recipes/registry";
import type { UploadServices } from "../upload/useUploadBatch";
import { Workbench } from "./Workbench";

interface WorkbenchPageProps {
  readonly uploadServices: UploadServices;
}

/** Rota da Bancada: a receita vem da URL e só segue se existir no registro. */
export function WorkbenchPage({ uploadServices }: WorkbenchPageProps): ReactElement {
  const recipe = findRecipe(useParams()["recipeId"] ?? "");
  if (recipe === null) {
    return <NotFoundPage />;
  }
  // A key recria a Bancada ao trocar de receita, sem herdar arquivos nem parâmetros da anterior.
  return <Workbench key={recipe.id} recipe={recipe} uploadServices={uploadServices} />;
}
