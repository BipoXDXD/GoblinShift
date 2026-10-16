import { useQuery } from "@tanstack/react-query";
import type { ReactElement, ReactNode } from "react";
import { useParams, useSearchParams } from "react-router";

import { useApiClient } from "../api/ApiClientContext";
import { NotFoundPage } from "../app/NotFoundPage";
import {
  CATALOG_QUERY_KEY,
  findCatalogVersion,
  getCatalog,
  type CatalogVersion,
} from "../catalog/api";
import { FailureNotice } from "../components/feedback/FailureNotice";
import { findRecipe, type RecipeForm, type RecipeModule } from "../recipes/registry";
import { TEXT } from "../text";
import type { UploadServices } from "../upload/useUploadBatch";
import {
  sourceFromSearch,
  useParameterSource,
  type ParameterSource,
  type SourceRef,
} from "./parameterSource";
import { Workbench, WorkbenchHeader } from "./Workbench";

interface WorkbenchPageProps {
  readonly uploadServices: UploadServices;
}

/** Rota da Bancada: a receita vem da URL e só segue se existir no registro. */
export function WorkbenchPage({ uploadServices }: WorkbenchPageProps): ReactElement {
  const recipe = findRecipe(useParams()["recipeId"] ?? "");
  const [search] = useSearchParams();
  const source = sourceFromSearch(search);
  if (recipe === null) {
    return <NotFoundPage />;
  }
  // A key recria a Bancada ao trocar de receita ou de origem, sem herdar arquivos nem parâmetros.
  return (
    <CatalogGate
      key={`${recipe.id}:${source?.kind ?? ""}:${source?.id ?? ""}`}
      recipe={recipe}
      source={source}
      uploadServices={uploadServices}
    />
  );
}

interface CatalogGateProps {
  readonly recipe: RecipeModule;
  /** Preset (`?preset=`) ou execução (`?execucao=`) que pré-preenche os parâmetros. */
  readonly source: SourceRef | null;
  readonly uploadServices: UploadServices;
}

/**
 * Defaults e limites da receita vêm de GET /catalog (2.2): a Bancada só abre com a versão do
 * formulário presente no catálogo e no formato que ele conhece.
 */
function CatalogGate({ recipe, source, uploadServices }: CatalogGateProps): ReactElement {
  const client = useApiClient();
  const loaded = useParameterSource(source);
  const catalog = useQuery({
    queryKey: CATALOG_QUERY_KEY,
    queryFn: ({ signal }) => getCatalog(client, signal),
    // Muda só com um deploy; o navegador revalida pelo ETag a cada carga da página.
    staleTime: Infinity,
  });
  const sourcePending = source !== null && loaded.isPending;
  if (catalog.data === undefined || sourcePending) {
    return (
      <div className="flex flex-col gap-8">
        <WorkbenchHeader title={TEXT.workbench.title(recipe.title)} />
        {catalog.data === undefined ? (
          <CatalogPending error={catalog.error} />
        ) : (
          <p className="text-fg-muted">
            {source?.kind === "run" ? TEXT.history.loadingRun : TEXT.presets.loadingPreset}
          </p>
        )}
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
  const prefilled =
    source === null || version === null
      ? { form, notice: null }
      : prefilledForm({
          recipe,
          version,
          fallback: form,
          source: loaded.data ?? null,
          error: loaded.error,
        });
  return (
    <Workbench
      recipe={recipe}
      form={prefilled.form}
      notice={prefilled.notice}
      uploadServices={uploadServices}
    />
  );
}

interface PrefillSources {
  readonly recipe: RecipeModule;
  readonly version: CatalogVersion;
  /** O formulário com os defaults do catálogo, para quando a origem não abre. */
  readonly fallback: RecipeForm;
  readonly source: ParameterSource | null;
  readonly error: Error | null;
}

/**
 * O formulário com os parâmetros da origem no lugar dos defaults do catálogo, e o aviso de onde
 * vieram. Origem que não abre (falha, outra receita, parâmetros apagados pela retenção, formato
 * desconhecido) deixa os padrões, com o motivo.
 */
function prefilledForm({ recipe, version, fallback, source, error }: PrefillSources): {
  readonly form: RecipeForm;
  readonly notice: ReactNode;
} {
  if (source === null) {
    return { form: fallback, notice: error === null ? null : <FailureNotice error={error} /> };
  }
  const sameRecipe = source.recipeId === recipe.id && source.recipeVersion === recipe.version;
  if (!sameRecipe) {
    return { form: fallback, notice: <SourceNotice>{source.wrongRecipeNotice}</SourceNotice> };
  }
  if (source.parameters === null) {
    return { form: fallback, notice: <SourceNotice>{source.goneNotice}</SourceNotice> };
  }
  const form = recipe.formFor({
    ...version,
    defaults: { ...version.defaults, ...source.parameters },
  });
  return form === null
    ? { form: fallback, notice: <SourceNotice>{source.wrongRecipeNotice}</SourceNotice> }
    : { form, notice: <SourceNotice>{source.loadedNotice}</SourceNotice> };
}

function SourceNotice({ children }: { readonly children: ReactNode }): ReactElement {
  return <p className="max-w-prose rounded-lg bg-info-bg p-4 text-info-fg">{children}</p>;
}

/** Enquanto não há catálogo: carregando, sem login (aviso, não erro) ou a falha em texto. */
function CatalogPending({ error }: { readonly error: Error | null }): ReactElement {
  return error === null ? (
    <p className="text-fg-muted">{TEXT.workbench.loadingCatalog}</p>
  ) : (
    <FailureNotice error={error} />
  );
}
