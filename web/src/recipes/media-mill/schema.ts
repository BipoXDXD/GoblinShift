import { z } from "zod";

import { formIssue, type FormField } from "../../components/a11y/ErrorSummary";
import { TEXT } from "../../text";
import type { RecipeDraft, RecipeParameters } from "../types";

// Formulário da variante media-mill v1. Tipos e opções vêm do código gerado da spec; defaults e
// limites, que o tipo não carrega, vêm de GET /catalog em tempo de execução (settings.ts).
type Parameters = RecipeParameters<"media-mill">;
type AudioFormat = NonNullable<Parameters["audio_format"]>;
type ThumbnailCount = NonNullable<Parameters["thumbnail_count"]>;

export const AUDIO_FORMATS = ["wav", "flac"] as const satisfies readonly AudioFormat[];
export const THUMBNAIL_COUNTS = [0, 1, 3] as const satisfies readonly ThumbnailCount[];

/** Valores do formulário com os nomes da API; o índice fica como o texto digitado. */
export interface MediaMillFormValues {
  readonly extract_audio: boolean;
  readonly audio_format: AudioFormat;
  /** Vazio é a primeira faixa de áudio de cada vídeo (`null` na API). */
  readonly audio_stream_index: string;
  readonly thumbnail_count: ThumbnailCount;
}

export interface MediaMillLimits {
  /** Maior `audio_stream_index` aceito; o menor é 0. */
  readonly maxStreamIndex: number;
  readonly thumbnailMaxSide: number;
  readonly maxSampleRateHz: number;
  readonly maxChannels: number;
}

export interface MediaMillSettings {
  readonly defaults: MediaMillFormValues;
  readonly limits: MediaMillLimits;
}

const ERRORS = TEXT.mediaMill.errors;
const INTEGER = /^\d+$/;

const streamIndex = (max: number) =>
  z
    .string()
    .trim()
    .transform((text, context) => {
      if (text === "") {
        return null;
      }
      const index = INTEGER.test(text) ? Number(text) : Number.NaN;
      if (!Number.isInteger(index) || index > max) {
        context.addIssue({ code: "custom", message: ERRORS.streamIndex(max) });
        return z.NEVER;
      }
      return index;
    });

function parametersSchema({ maxStreamIndex }: MediaMillLimits): z.ZodType<Required<Parameters>> {
  const option = { error: TEXT.recipeFields.invalidOption };
  return z
    .object({
      extract_audio: z.boolean(),
      audio_format: z.enum(AUDIO_FORMATS, option),
      audio_stream_index: streamIndex(maxStreamIndex),
      thumbnail_count: z.union(
        THUMBNAIL_COUNTS.map((count) => z.literal(count)),
        option,
      ),
    })
    .refine((parameters) => parameters.extract_audio || parameters.thumbnail_count > 0, {
      error: ERRORS.noOutput,
      path: ["extract_audio"],
    });
}

/**
 * Valida o formulário e monta o comando com todos os parâmetros explícitos, ou devolve os erros já
 * apontando o id de cada campo. Sem extrair áudio a API recusa formato e faixa fora do default
 * (1.5): eles vão com os defaults do catálogo, sem cobrar o que está desabilitado.
 */
export function parseMediaMill(
  values: MediaMillFormValues,
  { defaults, limits }: MediaMillSettings,
): RecipeDraft<"media-mill"> {
  const effective = values.extract_audio
    ? values
    : {
        ...values,
        audio_format: defaults.audio_format,
        audio_stream_index: defaults.audio_stream_index,
      };
  const result = parametersSchema(limits).safeParse(effective);
  if (!result.success) {
    return {
      ok: false,
      issues: result.error.issues.map((issue) =>
        formIssue(mediaMillField(issue.path.map(String)), issue.message),
      ),
    };
  }
  return {
    ok: true,
    command: { recipe_id: "media-mill", recipe_version: 1, parameters: result.data },
  };
}

const ID_PREFIX = "media-mill";
// Os mesmos rótulos do formulário (MediaMillFields): o resumo de erros cita o campo por eles.
const LABELS: Readonly<Record<keyof MediaMillFormValues, string>> = {
  extract_audio: TEXT.mediaMill.extractAudio,
  audio_format: TEXT.mediaMill.audioFormat,
  audio_stream_index: TEXT.mediaMill.audioStreamIndex,
  thumbnail_count: TEXT.mediaMill.thumbnailCount,
};

/** Controle para um caminho de erro relativo a `parameters`; `null` fora do formulário. */
export function mediaMillField(path: readonly (string | number)[]): FormField | null {
  const [field] = path.map(String);
  return field !== undefined && isFormField(field)
    ? { fieldId: fieldId(field), label: LABELS[field] }
    : null;
}

function isFormField(field: string): field is keyof MediaMillFormValues {
  return Object.hasOwn(LABELS, field);
}

export function fieldId(field: string): string {
  return `${ID_PREFIX}-${field}`;
}
