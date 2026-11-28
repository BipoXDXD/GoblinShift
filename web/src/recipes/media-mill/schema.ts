import { z } from "zod";

import { formIssue, type FormField } from "../../components/a11y/ErrorSummary";
import { TEXT } from "../../text";
import type { RecipeDraft, RecipeParameters } from "../types";

// Formulário da variante media-mill v2. Tipos e opções vêm do código gerado da spec; defaults,
// limites e bitrates por formato, que o tipo não carrega, vêm de GET /catalog em tempo de execução
// (settings.ts).
type Parameters = RecipeParameters<"media-mill">;
type AudioFormat = NonNullable<Parameters["audio_format"]>;
type ThumbnailCount = NonNullable<Parameters["thumbnail_count"]>;
export type BitrateKbps = NonNullable<Parameters["audio_bitrate_kbps"]>;

export const AUDIO_FORMATS = [
  "wav",
  "flac",
  "mp3",
  "aac",
  "opus",
] as const satisfies readonly AudioFormat[];
export const LOSSY_FORMATS = ["mp3", "aac", "opus"] as const satisfies readonly AudioFormat[];
export type LossyFormat = (typeof LOSSY_FORMATS)[number];
export const BITRATES_KBPS = [
  64, 96, 128, 160, 192, 256, 320,
] as const satisfies readonly BitrateKbps[];
export const THUMBNAIL_COUNTS = [0, 1, 3] as const satisfies readonly ThumbnailCount[];

/**
 * Valores do formulário com os nomes da API, menos o trecho, que fica como o texto digitado
 * (`m:ss`) e vira `trim_start_ms`/`trim_end_ms` na validação. O índice também fica como texto.
 */
export interface MediaMillFormValues {
  readonly extract_audio: boolean;
  readonly audio_format: AudioFormat;
  /** Vazio é a primeira faixa de áudio de cada vídeo (`null` na API). */
  readonly audio_stream_index: string;
  readonly thumbnail_count: ThumbnailCount;
  readonly audio_bitrate_kbps: BitrateKbps;
  /** Vazio é o começo do vídeo. */
  readonly trim_start: string;
  /** Vazio vai até o final do vídeo (`null` na API). */
  readonly trim_end: string;
}

export interface MediaMillLimits {
  /** Maior `audio_stream_index` aceito; o menor é 0. */
  readonly maxStreamIndex: number;
  readonly thumbnailMaxSide: number;
  readonly maxSampleRateHz: number;
  readonly maxChannels: number;
  /** Maior ponto do trecho, em milissegundos. */
  readonly maxTrimMs: number;
}

export interface MediaMillSettings {
  readonly defaults: MediaMillFormValues;
  readonly limits: MediaMillLimits;
  /** Bitrates aceitos por formato com perda, na ordem do schema. */
  readonly bitratesByFormat: Readonly<Record<LossyFormat, readonly BitrateKbps[]>>;
}

export function isLossy(format: AudioFormat): format is LossyFormat {
  return LOSSY_FORMATS.some((lossy) => lossy === format);
}

const ERRORS = TEXT.mediaMill.errors;
const INTEGER = /^\d+$/;
// `ss` ou `m:ss`, com até três casas depois da vírgula (milissegundos).
const TRIM = /^(?:(\d+):([0-5]\d)|(\d+))(?:,(\d{1,3}))?$/;
const MS_PER_SECOND = 1000;
const SECONDS_PER_MINUTE = 60;
const MS_PER_MINUTE = SECONDS_PER_MINUTE * MS_PER_SECOND;
const FRACTION_DIGITS = 3;

/**
 * Um ponto do vídeo digitado como `ss`, `m:ss` ou `m:ss,d` (até milissegundos), em ms. Vazio ou
 * fora do formato é `null`; quem chama distingue os dois pelo texto.
 */
export function parseTrim(text: string): number | null {
  const match = TRIM.exec(text.trim());
  if (match === null) {
    return null;
  }
  const [, minutes, seconds, onlySeconds, fraction] = match;
  const wholeSeconds =
    onlySeconds === undefined
      ? Number(minutes) * SECONDS_PER_MINUTE + Number(seconds)
      : Number(onlySeconds);
  const ms = fraction === undefined ? 0 : Number(fraction.padEnd(FRACTION_DIGITS, "0"));
  return wholeSeconds * MS_PER_SECOND + ms;
}

/** Milissegundos como `m:ss`, com a fração depois da vírgula só quando há. */
export function formatTrim(ms: number): string {
  const minutes = Math.floor(ms / MS_PER_MINUTE);
  const seconds = Math.floor((ms % MS_PER_MINUTE) / MS_PER_SECOND);
  const fraction = String(ms % MS_PER_SECOND)
    .padStart(FRACTION_DIGITS, "0")
    .replace(/0+$/, "");
  const clock = `${String(minutes)}:${String(seconds).padStart(2, "0")}`;
  return fraction === "" ? clock : `${clock},${fraction}`;
}

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

/** Ponto do trecho em ms; vazio vira `empty` (0 no início, `null` no fim). */
const trimPoint = <E extends number | null>(maxMs: number, empty: E) =>
  z
    .string()
    .trim()
    .transform((text, context): number | E => {
      if (text === "") {
        return empty;
      }
      const ms = parseTrim(text);
      if (ms === null || ms > maxMs) {
        context.addIssue({ code: "custom", message: ERRORS.trim(formatTrim(maxMs)) });
        return z.NEVER;
      }
      return ms;
    });

function formSchema({ limits, bitratesByFormat }: MediaMillSettings) {
  const option = { error: TEXT.recipeFields.invalidOption };
  return z
    .object({
      extract_audio: z.boolean(),
      audio_format: z.enum(AUDIO_FORMATS, option),
      audio_stream_index: streamIndex(limits.maxStreamIndex),
      thumbnail_count: z.union(
        THUMBNAIL_COUNTS.map((count) => z.literal(count)),
        option,
      ),
      audio_bitrate_kbps: z.union(
        BITRATES_KBPS.map((bitrate) => z.literal(bitrate)),
        option,
      ),
      trim_start: trimPoint(limits.maxTrimMs, 0),
      trim_end: trimPoint(limits.maxTrimMs, null),
    })
    .refine((values) => values.extract_audio || values.thumbnail_count > 0, {
      error: ERRORS.noOutput,
      path: ["extract_audio"],
    })
    .refine(
      (values) =>
        !isLossy(values.audio_format) ||
        bitratesByFormat[values.audio_format].includes(values.audio_bitrate_kbps),
      { error: TEXT.recipeFields.invalidOption, path: ["audio_bitrate_kbps"] },
    )
    .refine((values) => values.trim_end === null || values.trim_end > values.trim_start, {
      error: ERRORS.trimEndBeforeStart,
      path: ["trim_end"],
    })
    .transform(({ trim_start, trim_end, ...rest }): Required<Parameters> => ({
      ...rest,
      trim_start_ms: trim_start,
      trim_end_ms: trim_end,
    }));
}

/**
 * Valida o formulário e monta o comando com todos os parâmetros explícitos, ou devolve os erros já
 * apontando o id de cada campo. A API recusa formato, faixa e bitrate fora do default sem extrair
 * áudio, e bitrate fora do default em WAV e FLAC (ADR 063): eles vão com os defaults do catálogo,
 * sem cobrar o que está desabilitado. O trecho vale também só para as miniaturas.
 */
export function parseMediaMill(
  values: MediaMillFormValues,
  settings: MediaMillSettings,
): RecipeDraft<"media-mill"> {
  const { defaults } = settings;
  const audioOn = values.extract_audio
    ? values
    : {
        ...values,
        audio_format: defaults.audio_format,
        audio_stream_index: defaults.audio_stream_index,
      };
  const effective = isLossy(audioOn.audio_format)
    ? audioOn
    : { ...audioOn, audio_bitrate_kbps: defaults.audio_bitrate_kbps };
  const result = formSchema(settings).safeParse(effective);
  if (!result.success) {
    // As regras de mais de um campo (refine) só rodam com os campos válidos: os erros já saem
    // um por campo, sem repetir.
    return {
      ok: false,
      issues: result.error.issues.map((issue) =>
        formIssue(mediaMillField(issue.path.map(String)), issue.message),
      ),
    };
  }
  return {
    ok: true,
    command: { recipe_id: "media-mill", recipe_version: 2, parameters: result.data },
  };
}

const ID_PREFIX = "media-mill";
// Os mesmos rótulos do formulário (MediaMillFields): o resumo de erros cita o campo por eles.
const LABELS: Readonly<Record<keyof MediaMillFormValues, string>> = {
  extract_audio: TEXT.mediaMill.extractAudio,
  audio_format: TEXT.mediaMill.audioFormat,
  audio_stream_index: TEXT.mediaMill.audioStreamIndex,
  thumbnail_count: TEXT.mediaMill.thumbnailCount,
  audio_bitrate_kbps: TEXT.mediaMill.audioBitrate,
  trim_start: TEXT.mediaMill.trimStart,
  trim_end: TEXT.mediaMill.trimEnd,
};

// O nome na API dos campos que o formulário guarda como texto com outro nome.
const API_NAMES: ReadonlyMap<string, keyof MediaMillFormValues> = new Map([
  ["trim_start_ms", "trim_start"],
  ["trim_end_ms", "trim_end"],
]);

/** Controle para um caminho de erro relativo a `parameters`; `null` fora do formulário. */
export function mediaMillField(path: readonly (string | number)[]): FormField | null {
  const [first] = path.map(String);
  const field = first === undefined ? undefined : (API_NAMES.get(first) ?? first);
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
