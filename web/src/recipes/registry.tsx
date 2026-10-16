import type { ReactElement } from "react";

import type { CatalogVersion } from "../catalog/api";
import { inputLimitsOf, type InputLimits } from "../catalog/inputLimits";
import { TEXT } from "../text";
import { CsvJanitorFields } from "./csv-janitor/CsvJanitorFields";
import { csvJanitorFieldId, parseCsvJanitor } from "./csv-janitor/schema";
import { csvJanitorSettings } from "./csv-janitor/settings";
import { DownloadsDetoxFields } from "./downloads-detox/DownloadsDetoxFields";
import { downloadsDetoxFieldId, parseDownloadsDetox } from "./downloads-detox/schema";
import { downloadsDetoxSettings } from "./downloads-detox/settings";
import { ImageForgeFields } from "./image-forge/ImageForgeFields";
import { PdfPressFields } from "./pdf-press/PdfPressFields";
import { parsePdfPress, pdfPressFieldId } from "./pdf-press/schema";
import { pdfPressSettings } from "./pdf-press/settings";
import { imageForgeFieldId, parseImageForge } from "./image-forge/schema";
import { imageForgeSettings } from "./image-forge/settings";
import { MediaMillFields } from "./media-mill/MediaMillFields";
import { mediaMillFieldId, parseMediaMill } from "./media-mill/schema";
import { mediaMillSettings } from "./media-mill/settings";
import type {
  RecipeCommandFor,
  RecipeDraft,
  RecipeFieldsProps,
  RecipeFormProps,
  RecipeId,
} from "./types";

/** Formulário de uma receita já ligado aos defaults e limites da versão no catálogo. */
export interface RecipeForm {
  readonly initialDraft: RecipeDraft<RecipeId>;
  readonly inputLimits: InputLimits;
  readonly renderFields: (props: RecipeFormProps<RecipeId>) => ReactElement;
}

/** O que a Bancada precisa de cada receita: o formulário escrito à mão e como apontar seus erros. */
export interface RecipeModule<K extends RecipeId = RecipeId> {
  readonly id: K;
  readonly version: RecipeCommandFor<K>["recipe_version"];
  readonly title: string;
  /** Atributo `accept` do input de arquivos; `null` aceita qualquer tipo. */
  readonly accept: string | null;
  /** A ordem das entradas muda o resultado (merge do PDF Press): a lista mostra a posição. */
  readonly numberedInputs?: true;
  /** `null` quando o catálogo não traz a versão num formato que o formulário conheça. */
  readonly formFor: (version: CatalogVersion) => RecipeForm | null;
  /** Id do controle para um caminho relativo a `parameters`; `null` se não for um campo. */
  readonly fieldIdFor: (path: readonly string[]) => string | null;
}

interface FormParts<K extends RecipeId, S> {
  readonly settingsOf: (version: CatalogVersion) => S | null;
  readonly initialDraft: (settings: S) => RecipeDraft<K>;
  readonly Fields: (props: RecipeFieldsProps<K, S>) => ReactElement;
}

function catalogForm<K extends RecipeId, S>({
  settingsOf,
  initialDraft,
  Fields,
}: FormParts<K, S>): (version: CatalogVersion) => RecipeForm | null {
  return (version) => {
    const settings = settingsOf(version);
    const inputLimits = inputLimitsOf(version);
    if (settings === null || inputLimits === null) {
      return null;
    }
    return {
      initialDraft: initialDraft(settings),
      inputLimits,
      renderFields: ({ errors, onDraftChange, inputs }) => (
        <Fields settings={settings} errors={errors} onDraftChange={onDraftChange} inputs={inputs} />
      ),
    };
  };
}

// Um formulário por recipe_id da spec: uma receita nova na API quebra o build aqui até ganhar o
// dela.
const RECIPES: { readonly [K in RecipeId]: RecipeModule<K> } = {
  "csv-janitor": {
    id: "csv-janitor",
    version: 1,
    title: TEXT.csvJanitor.title,
    accept: ".csv,text/csv",
    formFor: catalogForm({
      settingsOf: csvJanitorSettings,
      initialDraft: (settings) => parseCsvJanitor(settings.defaults, settings.limits),
      Fields: CsvJanitorFields,
    }),
    fieldIdFor: csvJanitorFieldId,
  },
  "image-forge": {
    id: "image-forge",
    version: 1,
    title: TEXT.imageForge.title,
    // MPO (fotos de celular) costuma vir com extensão .jpg; .mpo cobre o resto (1.2).
    accept: ".jpg,.jpeg,.png,.mpo,image/jpeg,image/png",
    formFor: catalogForm({
      settingsOf: imageForgeSettings,
      initialDraft: (settings) => parseImageForge(settings.defaults, settings),
      Fields: ImageForgeFields,
    }),
    fieldIdFor: imageForgeFieldId,
  },
  "downloads-detox": {
    id: "downloads-detox",
    version: 1,
    title: TEXT.downloadsDetox.title,
    // Aceita tipos variados e classifica pelo conteúdo (1.4); recusa o que não reconhece.
    accept: null,
    formFor: catalogForm({
      settingsOf: downloadsDetoxSettings,
      initialDraft: (settings) => parseDownloadsDetox(settings.defaults, settings),
      Fields: DownloadsDetoxFields,
    }),
    fieldIdFor: downloadsDetoxFieldId,
  },
  "pdf-press": {
    id: "pdf-press",
    version: 1,
    title: TEXT.pdfPress.title,
    // A assinatura %PDF- é conferida no executor; aqui é só o filtro da janela de arquivos.
    accept: ".pdf,application/pdf",
    numberedInputs: true,
    formFor: catalogForm({
      settingsOf: pdfPressSettings,
      initialDraft: (settings) => parsePdfPress(settings.defaults, settings, []),
      Fields: PdfPressFields,
    }),
    fieldIdFor: pdfPressFieldId,
  },
  "media-mill": {
    id: "media-mill",
    version: 1,
    title: TEXT.mediaMill.title,
    // Só os contêineres da allowlist (1.5); o conteúdo é conferido pelo ffprobe no executor.
    accept: ".mp4,.mov,.webm,video/mp4,video/quicktime,video/webm",
    formFor: catalogForm({
      settingsOf: mediaMillSettings,
      initialDraft: (settings) => parseMediaMill(settings.defaults, settings),
      Fields: MediaMillFields,
    }),
    fieldIdFor: mediaMillFieldId,
  },
};

/** Receitas na ordem de exibição. */
export const RECIPE_LIST: readonly RecipeModule[] = Object.values(RECIPES);

const BY_ID: ReadonlyMap<string, RecipeModule> = new Map(
  RECIPE_LIST.map((recipe) => [recipe.id, recipe]),
);

export function findRecipe(id: string): RecipeModule | null {
  return BY_ID.get(id) ?? null;
}
