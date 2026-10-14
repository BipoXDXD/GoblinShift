import { z } from "zod";

import type { FormIssue } from "../../components/a11y/ErrorSummary";
import { TEXT } from "../../text";
import type { RecipeDraft, RecipeParameters } from "../types";

// Formulário da variante image-forge v1. Tipos e opções vêm do código gerado da spec; defaults e
// limites, que o tipo não carrega, vêm de GET /catalog em tempo de execução (settings.ts).
type Parameters = RecipeParameters<"image-forge">;
type OutputFormat = NonNullable<Parameters["output_format"]>;

export const OUTPUT_FORMATS = [
  "preserve",
  "jpeg",
  "png",
] as const satisfies readonly OutputFormat[];

/** Valores do formulário com os nomes da API; números ficam como o texto digitado. */
export interface ImageForgeFormValues {
  readonly max_width: string;
  readonly max_height: string;
  readonly output_format: OutputFormat;
  readonly jpeg_quality: string;
  readonly jpeg_background: string;
  readonly make_thumbnails: boolean;
}

/** Faixa fechada de um parâmetro inteiro. */
export interface IntegerRange {
  readonly min: number;
  readonly max: number;
}

export interface ImageForgeLimits {
  readonly width: IntegerRange;
  readonly height: IntegerRange;
  readonly quality: IntegerRange;
  /** Lado máximo da miniatura, em pixels. */
  readonly thumbnailBox: number;
}

export interface ImageForgeSettings {
  readonly defaults: ImageForgeFormValues;
  readonly limits: ImageForgeLimits;
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
}: ImageForgeLimits): z.ZodType<Required<Parameters>> {
  return z.object({
    max_width: integerIn(width),
    max_height: integerIn(height),
    output_format: z.enum(OUTPUT_FORMATS, { error: TEXT.recipeFields.invalidOption }),
    jpeg_quality: integerIn(quality),
    jpeg_background: hexColor,
    make_thumbnails: z.boolean(),
  });
}

/**
 * Valida o formulário e monta o comando com todos os parâmetros explícitos, ou devolve os erros já
 * apontando o id de cada campo. Com saída PNG nenhuma imagem sai JPEG e a API recusa opções de
 * JPEG fora do default (1.2): elas vão com os defaults do catálogo, sem cobrar o que está oculto.
 */
export function parseImageForge(
  values: ImageForgeFormValues,
  { defaults, limits }: ImageForgeSettings,
): RecipeDraft<"image-forge"> {
  const effective =
    values.output_format === "png"
      ? {
          ...values,
          jpeg_quality: defaults.jpeg_quality,
          jpeg_background: defaults.jpeg_background,
        }
      : values;
  const result = parametersSchema(limits).safeParse(effective);
  if (!result.success) {
    return {
      ok: false,
      issues: result.error.issues.map((issue): FormIssue => ({
        fieldId: imageForgeFieldId(issue.path.map(String)),
        message: issue.message,
      })),
    };
  }
  return {
    ok: true,
    command: { recipe_id: "image-forge", recipe_version: 1, parameters: result.data },
  };
}

const ID_PREFIX = "image-forge";
const FIELDS: ReadonlySet<string> = new Set<keyof ImageForgeFormValues>([
  "max_width",
  "max_height",
  "output_format",
  "jpeg_quality",
  "jpeg_background",
  "make_thumbnails",
]);

/** Id do controle para um caminho de erro relativo a `parameters`; `null` fora do formulário. */
export function imageForgeFieldId(path: readonly (string | number)[]): string | null {
  const [field] = path.map(String);
  return field !== undefined && FIELDS.has(field) ? fieldId(field) : null;
}

export function fieldId(field: string): string {
  return `${ID_PREFIX}-${field}`;
}
