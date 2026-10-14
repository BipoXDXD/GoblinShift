import { useQuery } from "@tanstack/react-query";
import type { ReactElement } from "react";
import { useParams } from "react-router";

import { useApiClient } from "../api/ApiClientContext";
import { ApiError } from "../api/response";
import { NotFoundPage } from "../app/NotFoundPage";
import { CATALOG_QUERY_KEY, findCatalogVersion, getCatalog } from "../catalog/api";
import { FocusHeading } from "../components/a11y/FocusHeading";
import { ProblemAlert } from "../components/feedback/ProblemAlert";
import { findRecipe, type RecipeModule } from "../recipes/registry";
import { TEXT } from "../text";
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
  return <CatalogGate key={recipe.id} recipe={recipe} uploadServices={uploadServices} />;
}

interface CatalogGateProps {
  readonly recipe: RecipeModule;
  readonly uploadServices: UploadServices;
}

/**
 * Defaults e limites da receita vêm de GET /catalog (2.2): a Bancada só abre com a versão do
 * formulário presente no catálogo e no formato que ele conhece.
 */
function CatalogGate({ recipe, uploadServices }: CatalogGateProps): ReactElement {
  const client = useApiClient();
  const catalog = useQuery({
    queryKey: CATALOG_QUERY_KEY,
    queryFn: ({ signal }) => getCatalog(client, signal),
    // Muda só com um deploy; o navegador revalida pelo ETag a cada carga da página.
    staleTime: Infinity,
  });
  if (catalog.data === undefined) {
    return (
      <main>
        <FocusHeading>{TEXT.workbench.title(recipe.title)}</FocusHeading>
        {catalog.error === null ? (
          <p>{TEXT.workbench.loadingCatalog}</p>
        ) : (
          <ProblemAlert
            failure={
              catalog.error instanceof ApiError ? catalog.error.failure : { kind: "internal" }
            }
          />
        )}
      </main>
    );
  }
  const version = findCatalogVersion(catalog.data, recipe.id, recipe.version);
  const form = version === null ? null : recipe.formFor(version);
  if (form === null) {
    return (
      <main>
        <FocusHeading>{TEXT.workbench.title(recipe.title)}</FocusHeading>
        <p role="alert">{TEXT.workbench.recipeUnavailable}</p>
      </main>
    );
  }
  return <Workbench recipe={recipe} form={form} uploadServices={uploadServices} />;
}
