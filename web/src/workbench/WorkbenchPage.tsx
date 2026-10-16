import { useQuery } from "@tanstack/react-query";
import type { ReactElement, ReactNode } from "react";
import { useParams, useSearchParams } from "react-router";
import { z } from "zod";

import { useApiClient } from "../api/ApiClientContext";
import { NotFoundPage } from "../app/NotFoundPage";
import { PRESET_PARAM } from "../app/paths";
import {
  CATALOG_QUERY_KEY,
  findCatalogVersion,
  getCatalog,
  type CatalogVersion,
} from "../catalog/api";
import { FailureNotice } from "../components/feedback/FailureNotice";
import { getPreset, PRESETS_QUERY_KEY, type Preset } from "../presets/api";
import {
  findRecipe,
  withoutUploadBoundParameters,
  type RecipeForm,
  type RecipeModule,
} from "../recipes/registry";
import { TEXT } from "../text";
import type { UploadServices } from "../upload/useUploadBatch";
import { Workbench, WorkbenchHeader } from "./Workbench";

interface WorkbenchPageProps {
  readonly uploadServices: UploadServices;
}

/** Rota da Bancada: a receita vem da URL e só segue se existir no registro. */
export function WorkbenchPage({ uploadServices }: WorkbenchPageProps): ReactElement {
  const recipe = findRecipe(useParams()["recipeId"] ?? "");
  const [search] = useSearchParams();
  // Id fora do formato é ignorado: a Bancada abre com os padrões, sem consultar a API.
  const parsedPreset = presetIdSchema.safeParse(search.get(PRESET_PARAM));
  const presetId = parsedPreset.success ? parsedPreset.data : null;
  if (recipe === null) {
    return <NotFoundPage />;
  }
  // A key recria a Bancada ao trocar de receita ou de preset, sem herdar arquivos nem parâmetros.
  return (
    <CatalogGate
      key={`${recipe.id}:${presetId ?? ""}`}
      recipe={recipe}
      presetId={presetId}
      uploadServices={uploadServices}
    />
  );
}

const presetIdSchema = z.uuid();

interface CatalogGateProps {
  readonly recipe: RecipeModule;
  /** Preset que pré-preenche os parâmetros (`?preset=`), ou `null`. */
  readonly presetId: string | null;
  readonly uploadServices: UploadServices;
}

/**
 * Defaults e limites da receita vêm de GET /catalog (2.2): a Bancada só abre com a versão do
 * formulário presente no catálogo e no formato que ele conhece.
 */
function CatalogGate({ recipe, presetId, uploadServices }: CatalogGateProps): ReactElement {
  const client = useApiClient();
  const preset = useQuery({
    queryKey: [...PRESETS_QUERY_KEY, presetId] as const,
    queryFn: ({ signal }) => getPreset(client, presetId ?? "", signal),
    enabled: presetId !== null,
    retry: false,
  });
  const catalog = useQuery({
    queryKey: CATALOG_QUERY_KEY,
    queryFn: ({ signal }) => getCatalog(client, signal),
    // Muda só com um deploy; o navegador revalida pelo ETag a cada carga da página.
    staleTime: Infinity,
  });
  const presetPending = presetId !== null && preset.isPending;
  if (catalog.data === undefined || presetPending) {
    return (
      <div className="flex flex-col gap-8">
        <WorkbenchHeader title={TEXT.workbench.title(recipe.title)} />
        {catalog.data === undefined ? (
          <CatalogPending error={catalog.error} />
        ) : (
          <p className="text-fg-muted">{TEXT.presets.loadingPreset}</p>
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
    presetId === null || version === null
      ? { form, notice: null }
      : presetForm({
          recipe,
          version,
          fallback: form,
          preset: preset.data ?? null,
          error: preset.error,
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

/**
 * O formulário com os parâmetros do preset no lugar dos defaults do catálogo, e o aviso de onde
 * vieram. Preset que não abre (falha, outra receita, formato desconhecido) deixa os padrões.
 */
interface PresetFormSources {
  readonly recipe: RecipeModule;
  readonly version: CatalogVersion;
  /** O formulário com os defaults do catálogo, para quando o preset não abre. */
  readonly fallback: RecipeForm;
  readonly preset: Preset | null;
  readonly error: Error | null;
}

function presetForm({ recipe, version, fallback, preset, error }: PresetFormSources): {
  readonly form: RecipeForm;
  readonly notice: ReactNode;
} {
  if (preset === null) {
    return { form: fallback, notice: error === null ? null : <FailureNotice error={error} /> };
  }
  const sameRecipe = preset.recipe_id === recipe.id && preset.recipe_version === recipe.version;
  const reusable = withoutUploadBoundParameters(recipe, preset.parameters);
  const form = sameRecipe
    ? recipe.formFor({ ...version, defaults: { ...version.defaults, ...reusable.parameters } })
    : null;
  if (form === null) {
    return { form: fallback, notice: <PresetNotice>{TEXT.presets.wrongRecipe}</PresetNotice> };
  }
  return {
    form,
    notice: (
      <PresetNotice>
        {TEXT.presets.loaded(preset.name)}
        {reusable.dropped && ` ${TEXT.workbench.uploadBoundDropped}`}
      </PresetNotice>
    ),
  };
}

/** Status (WCAG 4.1.3): o aviso chega depois do carregamento do preset, no lugar do "carregando". */
function PresetNotice({ children }: { readonly children: ReactNode }): ReactElement {
  return (
    <p role="status" className="max-w-prose rounded-lg bg-info-bg p-4 text-info-fg">
      {children}
    </p>
  );
}

/** Enquanto não há catálogo: carregando, sem login (aviso, não erro) ou a falha em texto. */
function CatalogPending({ error }: { readonly error: Error | null }): ReactElement {
  return error === null ? (
    <p className="text-fg-muted">{TEXT.workbench.loadingCatalog}</p>
  ) : (
    <FailureNotice error={error} />
  );
}
