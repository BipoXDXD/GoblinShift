import { z } from "zod";

import type { FormIssue } from "../../components/a11y/ErrorSummary";
import { TEXT } from "../../text";
import type { RecipeDraft, RecipeInput, RecipeParameters } from "../types";

// Formulário da variante pdf-press v1. Tipos e opções vêm do código gerado da spec; defaults e
// limites, que o tipo não carrega, vêm de GET /catalog em tempo de execução (settings.ts).
type Parameters = RecipeParameters<"pdf-press">;
type Operation = NonNullable<Parameters["operation"]>;

export const OPERATIONS = ["merge", "split", "select"] as const satisfies readonly Operation[];

/** Um cartão da seleção: o PDF enviado e a página, como o texto digitado. */
export interface PageCard {
  /** Identidade estável do cartão na lista, para reordenar sem perder o foco. */
  readonly key: string;
  /** `""` enquanto nenhum PDF pronto foi escolhido. */
  readonly uploadId: string;
  readonly pageNumber: string;
}

/** Valores do formulário; números ficam como o texto digitado. */
export interface PdfPressFormValues {
  readonly operation: Operation;
  readonly pages_per_document: string;
  readonly cards: readonly PageCard[];
}

export interface PdfPressLimits {
  /** Maior grupo do split e maior número de página (o teto de páginas de entrada). */
  readonly maxPages: number;
  /** Quantos cartões a seleção aceita (o teto de páginas de saída). */
  readonly maxSelectedPages: number;
}

export interface PdfPressSettings {
  readonly defaults: PdfPressFormValues;
  readonly limits: PdfPressLimits;
}

const ERRORS = TEXT.pdfPress.errors;
const INTEGER = /^-?\d+$/;

const integerUpTo = (max: number) =>
  z
    .string()
    .trim()
    .regex(INTEGER, { error: ERRORS.integerBetween(1, max) })
    .transform(Number)
    .pipe(
      z
        .int()
        .min(1, { error: ERRORS.integerBetween(1, max) })
        .max(max, { error: ERRORS.integerBetween(1, max) }),
    );

function parametersSchema(
  { maxPages, maxSelectedPages }: PdfPressLimits,
  inputs: readonly RecipeInput[],
): z.ZodType<Parameters> {
  const uploadIds = new Set(inputs.map((input) => input.uploadId));
  const card = z.object({
    uploadId: z.string().refine((id) => uploadIds.has(id), { error: ERRORS.chooseFile }),
    pageNumber: integerUpTo(maxPages),
  });
  // Só o split leva pages_per_document e só o select leva a lista: a API recusa o resto (ADR 035).
  return z.discriminatedUnion("operation", [
    z.object({ operation: z.literal("merge") }).transform(() => ({ operation: "merge" as const })),
    z
      .object({ operation: z.literal("split"), pages_per_document: integerUpTo(maxPages) })
      .transform(({ pages_per_document }) => ({
        operation: "split" as const,
        pages_per_document,
      })),
    z
      .object({
        operation: z.literal("select"),
        cards: z
          .array(card)
          .min(1, { error: ERRORS.noPages })
          .max(maxSelectedPages, { error: ERRORS.tooManyPages(maxSelectedPages) }),
      })
      .transform(({ cards }) => ({
        operation: "select" as const,
        select: cards.map((chosen) => ({
          upload_id: chosen.uploadId,
          page_number: chosen.pageNumber,
        })),
      })),
  ]);
}

/**
 * Valida o formulário contra os PDFs prontos e monta o comando, ou devolve os erros já apontando
 * o id de cada campo.
 */
export function parsePdfPress(
  values: PdfPressFormValues,
  { limits }: PdfPressSettings,
  inputs: readonly RecipeInput[],
): RecipeDraft<"pdf-press"> {
  const result = parametersSchema(limits, inputs).safeParse(values);
  if (!result.success) {
    return {
      ok: false,
      issues: result.error.issues.map((issue): FormIssue => ({
        fieldId: formFieldId(issue.path.map(String)),
        message: issue.message,
      })),
    };
  }
  return {
    ok: true,
    command: { recipe_id: "pdf-press", recipe_version: 1, parameters: result.data },
  };
}

const ID_PREFIX = "pdf-press";
const CARD_PARTS: Readonly<Record<string, string>> = {
  uploadId: "upload_id",
  pageNumber: "page_number",
  upload_id: "upload_id",
  page_number: "page_number",
};

/** Caminho do formulário (`cards`) ou do servidor (`select`) → id do controle. */
function formFieldId(path: readonly string[]): string | null {
  const [field, index, part] = path;
  if (field === "operation" || field === "pages_per_document") {
    return fieldId(field);
  }
  if (field !== "cards" && field !== "select") {
    return null;
  }
  if (index === undefined) {
    return addCardButtonId();
  }
  const cardPart = part === undefined ? undefined : CARD_PARTS[part];
  return cardPart === undefined || !/^\d+$/.test(index)
    ? null
    : cardFieldId(Number(index), cardPart);
}

/** Id do controle para um caminho de erro do servidor, relativo a `parameters`. */
export function pdfPressFieldId(path: readonly (string | number)[]): string | null {
  return formFieldId(path.map(String));
}

export function fieldId(field: "operation" | "pages_per_document"): string {
  return `${ID_PREFIX}-${field}`;
}

export function cardFieldId(index: number, part: string): string {
  return `${ID_PREFIX}-select-${String(index)}-${part}`;
}

export function addCardButtonId(): string {
  return `${ID_PREFIX}-select-adicionar`;
}

/** Um documento de saída como a prévia o descreve: o nome e o que entra nele, na ordem. */
export interface PreviewDocument {
  readonly name: string;
  readonly entries: readonly string[];
}

const PREVIEW = TEXT.pdfPress.preview;

/**
 * Mapa de páginas previsto, sem abrir nenhum PDF (2.2): o navegador não sabe quantas páginas cada
 * arquivo tem, então merge e split falam de "todas as páginas" e o select lista os cartões.
 */
export function pageMapPreview(
  values: PdfPressFormValues,
  inputs: readonly RecipeInput[],
): readonly PreviewDocument[] {
  switch (values.operation) {
    case "merge":
      return inputs.length === 0
        ? []
        : [{ name: "merged.pdf", entries: inputs.map((input) => PREVIEW.allPages(input.name)) }];
    case "split":
      return inputs.map((input) => ({
        name: input.name,
        entries: [PREVIEW.groups(values.pages_per_document.trim())],
      }));
    case "select": {
      const names = new Map(inputs.map((input) => [input.uploadId, input.name]));
      const entries = values.cards.map((card) =>
        PREVIEW.page(names.get(card.uploadId) ?? PREVIEW.noFile, card.pageNumber.trim()),
      );
      return entries.length === 0 ? [] : [{ name: "selection.pdf", entries }];
    }
  }
}
