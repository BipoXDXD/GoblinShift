import { useQuery } from "@tanstack/react-query";
import type { ReactElement } from "react";
import { useParams } from "react-router";

import { useApiClient } from "../api/ApiClientContext";
import { NotFoundPage } from "../app/NotFoundPage";
import { CATALOG_QUERY_KEY, findCatalogVersion, getCatalog } from "../catalog/api";
import { QueryFailure } from "../components/feedback/QueryFailure";
import { findRecipe, type RecipeModule } from "../recipes/registry";
import { TEXT } from "../text";
import type { UploadServices } from "../upload/useUploadBatch";
import { Workbench, WorkbenchHeader } from "./Workbench";

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
      <div className="flex flex-col gap-8">
        <WorkbenchHeader title={TEXT.workbench.title(recipe.title)} />
        <CatalogPending error={catalog.error} />
      </div>
    );
  }
  const version = findCatalogVersion(catalog.data, recipe.id, recipe.version);
  const form = version === null ? null : recipe.formFor(version);
  if (form === null) {
    return (
      <div className="flex flex-col gap-8">
        <WorkbenchHeader title={TEXT.workbench.title(recipe.title)} />
        <p
          role="alert"
          className="rounded-lg border-l-4 border-danger-accent bg-danger-bg p-4 font-semibold text-danger-fg"
        >
          {TEXT.workbench.recipeUnavailable}
        </p>
      </div>
    );
  }
  return <Workbench recipe={recipe} form={form} uploadServices={uploadServices} />;
}

/** Enquanto não há catálogo: carregando, sem login (aviso, não erro) ou a falha em texto. */
function CatalogPending({ error }: { readonly error: Error | null }): ReactElement {
  return error === null ? (
    <p className="text-fg-muted">{TEXT.workbench.loadingCatalog}</p>
  ) : (
    <QueryFailure error={error} />
  );
}
