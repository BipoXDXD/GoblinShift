import { z } from "zod";

import { formIssue, type FormField } from "../../components/a11y/ErrorSummary";
import { TEXT } from "../../text";
import type { RecipeDraft, RecipeParameters } from "../types";

// Formulário da variante downloads-detox v1. Tipos e opções vêm do código gerado da spec; defaults
// e limites, que o tipo não carrega, vêm de GET /catalog em tempo de execução (settings.ts).
type Parameters = RecipeParameters<"downloads-detox">;
type GroupBy = NonNullable<Parameters["group_by"]>;
type NameMode = NonNullable<Parameters["name_mode"]>;

export const GROUP_BY_OPTIONS = ["kind", "none"] as const satisfies readonly GroupBy[];
export const NAME_MODES = ["preserve", "indexed"] as const satisfies readonly NameMode[];

export interface DownloadsDetoxFormValues {
  readonly deduplicate: boolean;
  readonly group_by: GroupBy;
  readonly name_mode: NameMode;
  /** Usado só com `name_mode: indexed`. */
  readonly name_prefix: string;
}

export interface DownloadsDetoxLimits {
  /** Em caracteres (pontos de código), como a API conta. */
  readonly maxPrefixChars: number;
}

export interface DownloadsDetoxSettings {
  readonly defaults: DownloadsDetoxFormValues;
  readonly limits: DownloadsDetoxLimits;
}

const ERRORS = TEXT.downloadsDetox.errors;
// Mesmas regras de `processing.downloads_detox.params` (1.4): o prefixo vira o início de um nome
// de arquivo, então nada de separador, controle, formato invisível nem arquivo oculto.
const SEPARATOR = /[/\\]/;
const INVISIBLE = /[\p{Cc}\p{Cf}]/u;

const namePrefix = (maxChars: number) =>
  z
    .string()
    .refine((prefix) => prefix !== "", { error: ERRORS.prefixRequired, abort: true })
    .refine((prefix) => Array.from(prefix).length <= maxChars, {
      error: ERRORS.prefixTooLong(maxChars),
      abort: true,
    })
    .refine((prefix) => !SEPARATOR.test(prefix), { error: ERRORS.prefixSeparator, abort: true })
    .refine((prefix) => !INVISIBLE.test(prefix), { error: ERRORS.prefixInvisible, abort: true })
    .refine((prefix) => !prefix.startsWith("."), { error: ERRORS.prefixDot, abort: true });

function parametersSchema({ maxPrefixChars }: DownloadsDetoxLimits): z.ZodType<Parameters> {
  const option = { error: TEXT.recipeFields.invalidOption };
  const common = {
    deduplicate: z.boolean(),
    group_by: z.enum(GROUP_BY_OPTIONS, option),
  };
  // A API recusa `name_prefix` enviado com `preserve` (A13): o campo só existe na numeração.
  return z.discriminatedUnion("name_mode", [
    z
      .object({ ...common, name_mode: z.literal("preserve"), name_prefix: z.string() })
      .transform(({ deduplicate, group_by, name_mode }) => ({ deduplicate, group_by, name_mode })),
    z.object({
      ...common,
      name_mode: z.literal("indexed"),
      name_prefix: namePrefix(maxPrefixChars),
    }),
  ]);
}

/**
 * Valida o formulário e monta o comando com os parâmetros explícitos, ou devolve os erros já
 * apontando o id de cada campo.
 */
export function parseDownloadsDetox(
  values: DownloadsDetoxFormValues,
  { limits }: DownloadsDetoxSettings,
): RecipeDraft<"downloads-detox"> {
  const result = parametersSchema(limits).safeParse(values);
  if (!result.success) {
    return {
      ok: false,
      issues: result.error.issues.map((issue) =>
        formIssue(downloadsDetoxField(issue.path.map(String)), issue.message),
      ),
    };
  }
  return {
    ok: true,
    command: { recipe_id: "downloads-detox", recipe_version: 1, parameters: result.data },
  };
}

const ID_PREFIX = "downloads-detox";
// Os mesmos rótulos do formulário (DownloadsDetoxFields): o resumo de erros cita o campo por eles.
const LABELS: Readonly<Record<keyof DownloadsDetoxFormValues, string>> = {
  deduplicate: TEXT.downloadsDetox.deduplicate,
  group_by: TEXT.downloadsDetox.groupBy,
  name_mode: TEXT.downloadsDetox.nameMode,
  name_prefix: TEXT.downloadsDetox.namePrefix,
};

/** Controle para um caminho de erro relativo a `parameters`; `null` fora do formulário. */
export function downloadsDetoxField(path: readonly (string | number)[]): FormField | null {
  const [field] = path.map(String);
  return field !== undefined && isFormField(field)
    ? { fieldId: fieldId(field), label: LABELS[field] }
    : null;
}

function isFormField(field: string): field is keyof DownloadsDetoxFormValues {
  return Object.hasOwn(LABELS, field);
}

export function fieldId(field: string): string {
  return `${ID_PREFIX}-${field}`;
}
