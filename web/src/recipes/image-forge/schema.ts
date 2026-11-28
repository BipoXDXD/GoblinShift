import { z } from "zod";

import { formIssue, type FormField } from "../../components/a11y/ErrorSummary";
import { TEXT } from "../../text";
import type { RecipeDraft, RecipeParameters } from "../types";

// Formulário da variante image-forge v2. Tipos e opções vêm do código gerado da spec; defaults e
// limites, que o tipo não carrega, vêm de GET /catalog em tempo de execução (settings.ts).
type Parameters = RecipeParameters<"image-forge">;
type OutputFormat = NonNullable<Parameters["output_format"]>;
type FitMode = NonNullable<Parameters["fit"]>;

export const OUTPUT_FORMATS = [
  "preserve",
  "jpeg",
  "png",
  "webp",
] as const satisfies readonly OutputFormat[];

export const FIT_MODES = ["contain", "cover", "pad", "scale"] as const satisfies readonly FitMode[];

/** Valores do formulário com os nomes da API; números ficam como o texto digitado. */
export interface ImageForgeFormValues {
  readonly max_width: string;
  readonly max_height: string;
  readonly output_format: OutputFormat;
  readonly jpeg_quality: string;
  readonly jpeg_background: string;
  readonly make_thumbnails: boolean;
  readonly fit: FitMode;
  readonly scale_percent: string;
  readonly webp_quality: string;
  readonly webp_lossless: boolean;
}

/** Controles que podem ficar desabilitados pela combinação, e o que cada um envia nesse caso. */
export type ImageForgeDisabledValues = Pick<
  ImageForgeFormValues,
  | "max_width"
  | "max_height"
  | "jpeg_quality"
  | "jpeg_background"
  | "scale_percent"
  | "webp_quality"
  | "webp_lossless"
>;

/** Faixa fechada de um parâmetro inteiro. */
export interface IntegerRange {
  readonly min: number;
  readonly max: number;
}

export interface ImageForgeLimits {
  readonly width: IntegerRange;
  readonly height: IntegerRange;
  readonly quality: IntegerRange;
  readonly scale: IntegerRange;
  readonly webpQuality: IntegerRange;
  /** Lado máximo da miniatura, em pixels. */
  readonly thumbnailBox: number;
}

export interface ImageForgeSettings {
  /** Valores iniciais: os defaults do catálogo, ou os de um preset ou execução reaproveitados. */
  readonly defaults: ImageForgeFormValues;
  /**
   * O que um controle desabilitado envia: o default do schema da versão, que a API exige nessa
   * combinação (1.2). Não sai de `defaults`, que pode vir de um preset com outros valores.
   */
  readonly disabledValues: ImageForgeDisabledValues;
  readonly limits: ImageForgeLimits;
}

/** Saídas sem nenhuma imagem em JPEG: as opções de JPEG somem. */
const WITHOUT_JPEG: ReadonlySet<OutputFormat> = new Set(["png", "webp"]);
/** Saídas sem nenhuma imagem em WebP: as opções de WebP ficam desabilitadas. */
const WITHOUT_WEBP: ReadonlySet<OutputFormat> = new Set(["jpeg", "png"]);

/** Quais controles a combinação atual desabilita (1.2: a API recusa o que não tem efeito). */
export function disabledControls(
  values: Pick<ImageForgeFormValues, "fit" | "output_format" | "webp_lossless">,
): {
  readonly box: boolean;
  readonly scale: boolean;
  readonly jpeg: boolean;
  readonly webp: boolean;
  readonly webpQuality: boolean;
} {
  const webp = WITHOUT_WEBP.has(values.output_format);
  return {
    box: values.fit === "scale",
    scale: values.fit !== "scale",
    jpeg: WITHOUT_JPEG.has(values.output_format),
    webp,
    webpQuality: webp || values.webp_lossless,
  };
}

const ERRORS = TEXT.imageForge.errors;
const INTEGER = /^-?\d+$/;
const HEX_COLOR = /^#[0-9A-Fa-f]{6}$/;

const integerIn = ({ min, max }: IntegerRange) =>
  z
    .string()
    .trim()
    .regex(INTEGER, { error: ERRORS.integerBetween(min, max) })
    .transform(Number)
    .pipe(
      z
        .int()
        .min(min, { error: ERRORS.integerBetween(min, max) })
        .max(max, { error: ERRORS.integerBetween(min, max) }),
    );

const hexColor = z
  .string()
  .regex(HEX_COLOR, { error: ERRORS.hexColor })
  .transform((color) => color.toUpperCase());

function parametersSchema({
  width,
  height,
  quality,
  scale,
  webpQuality,
}: ImageForgeLimits): z.ZodType<Required<Parameters>> {
  return z.object({
    max_width: integerIn(width),
    max_height: integerIn(height),
    output_format: z.enum(OUTPUT_FORMATS, { error: TEXT.recipeFields.invalidOption }),
    jpeg_quality: integerIn(quality),
    jpeg_background: hexColor,
    make_thumbnails: z.boolean(),
    fit: z.enum(FIT_MODES, { error: TEXT.recipeFields.invalidOption }),
    scale_percent: integerIn(scale),
    webp_quality: integerIn(webpQuality),
    webp_lossless: z.boolean(),
  });
}

/** Os valores com cada controle desabilitado trocado pelo que ele envia. */
function effectiveValues(
  values: ImageForgeFormValues,
  fallback: ImageForgeDisabledValues,
): ImageForgeFormValues {
  const disabled = disabledControls(values);
  return {
    ...values,
    ...(disabled.box ? { max_width: fallback.max_width, max_height: fallback.max_height } : {}),
    ...(disabled.scale ? { scale_percent: fallback.scale_percent } : {}),
    ...(disabled.jpeg
      ? { jpeg_quality: fallback.jpeg_quality, jpeg_background: fallback.jpeg_background }
      : {}),
    ...(disabled.webp ? { webp_lossless: fallback.webp_lossless } : {}),
    ...(disabled.webpQuality ? { webp_quality: fallback.webp_quality } : {}),
  };
}

/**
 * Valida o formulário e monta o comando com todos os parâmetros explícitos, ou devolve os erros já
 * apontando o id de cada campo. A API recusa opções sem efeito fora do default (1.2): um controle
 * desabilitado ou oculto vai com o default do schema, sem cobrar o que a pessoa não pode mudar.
 */
export function parseImageForge(
  values: ImageForgeFormValues,
  { disabledValues, limits }: ImageForgeSettings,
): RecipeDraft<"image-forge"> {
  const result = parametersSchema(limits).safeParse(effectiveValues(values, disabledValues));
  if (!result.success) {
    return {
      ok: false,
      issues: result.error.issues.map((issue) =>
        formIssue(imageForgeField(issue.path.map(String)), issue.message),
      ),
    };
  }
  return {
    ok: true,
    command: { recipe_id: "image-forge", recipe_version: 2, parameters: result.data },
  };
}

const ID_PREFIX = "image-forge";
// Os mesmos rótulos do formulário (ImageForgeFields): o resumo de erros cita o campo por eles.
const LABELS: Readonly<Record<keyof ImageForgeFormValues, string>> = {
  max_width: TEXT.imageForge.maxWidth,
  max_height: TEXT.imageForge.maxHeight,
  output_format: TEXT.imageForge.outputFormat,
  jpeg_quality: TEXT.imageForge.jpegQuality,
  jpeg_background: TEXT.imageForge.jpegBackground,
  make_thumbnails: TEXT.imageForge.makeThumbnails,
  fit: TEXT.imageForge.fit,
  scale_percent: TEXT.imageForge.scalePercent,
  webp_quality: TEXT.imageForge.webpQuality,
  webp_lossless: TEXT.imageForge.webpLossless,
};

/** Controle para um caminho de erro relativo a `parameters`; `null` fora do formulário. */
export function imageForgeField(path: readonly (string | number)[]): FormField | null {
  const [field] = path.map(String);
  return field !== undefined && isFormField(field)
    ? { fieldId: fieldId(field), label: LABELS[field] }
    : null;
}

function isFormField(field: string): field is keyof ImageForgeFormValues {
  return Object.hasOwn(LABELS, field);
}

export function fieldId(field: string): string {
  return `${ID_PREFIX}-${field}`;
}
