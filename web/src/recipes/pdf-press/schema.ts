import { z } from "zod";

import { formIssue, type FormField } from "../../components/a11y/ErrorSummary";
import { TEXT } from "../../text";
import type { RecipeDraft, RecipeInput, RecipeParametersAt } from "../types";
import { parsePageRanges, type PageRangeText } from "./pageRanges";

// Formulário da variante pdf-press v2 (ADR 070). Tipos e opções vêm do código gerado da spec;
// defaults e limites, que o tipo não carrega, vêm de GET /catalog em tempo de execução
// (settings.ts). Presets v1 abrem aqui sem o `select`, que cita uploads de outra execução.
type Parameters = RecipeParametersAt<"pdf-press", 2>;
type Operation = NonNullable<Parameters["operation"]>;
export type Rotation = NonNullable<Parameters["rotation"]>;
export type Compression = NonNullable<Parameters["compression"]>;
type RangeParameters = NonNullable<Parameters["select"]>[number];

export const OPERATIONS = ["merge", "split", "select"] as const satisfies readonly Operation[];
export const ROTATIONS = [0, 90, 180, 270] as const satisfies readonly Rotation[];
export const COMPRESSIONS = ["none", "lossless"] as const satisfies readonly Compression[];

/** Um cartão da seleção: o PDF enviado, as páginas como o texto digitado e a rotação delas. */
export interface PageCard {
  /** Identidade estável do cartão na lista, para reordenar sem perder o foco. */
  readonly key: string;
  /** `""` enquanto nenhum PDF pronto foi escolhido. */
  readonly uploadId: string;
  /** Intervalos como `1-3, 7, 10-` (pageRanges.ts). */
  readonly pages: string;
  readonly rotation: Rotation;
}

/** Valores do formulário; números e intervalos ficam como o texto digitado. */
export interface PdfPressFormValues {
  readonly operation: Operation;
  readonly pages_per_document: string;
  /** Só vai no comando de merge e split; no select, cada cartão tem a sua. */
  readonly rotation: Rotation;
  readonly compression: Compression;
  readonly strip_metadata: boolean;
  readonly cards: readonly PageCard[];
}

export interface PdfPressLimits {
  /** Maior grupo do split e maior número de página (o teto de páginas de entrada). */
  readonly maxPages: number;
  /** Quantos intervalos o `select` aceita (o `maxItems` do schema). */
  readonly maxRanges: number;
  /** Teto de páginas de saída: a soma dos intervalos fechados não pode passar dele. */
  readonly maxOutputPages: number;
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

const pagesUpTo = (max: number) =>
  z.string().transform((text, context): readonly PageRangeText[] => {
    const result = parsePageRanges(text, max);
    if (!result.ok) {
      context.addIssue({ code: "custom", message: result.message });
      return z.NEVER;
    }
    return result.ranges;
  });

/** Páginas que os intervalos fechados somam; o fim de `N-` só o executor conhece. */
function closedPages(select: readonly RangeParameters[]): number {
  return select.reduce(
    (total, range) =>
      total + (range.last_page === null ? 0 : range.last_page - range.first_page + 1),
    0,
  );
}

function parametersSchema(
  { maxPages, maxRanges, maxOutputPages }: PdfPressLimits,
  inputs: readonly RecipeInput[],
): z.ZodType<Parameters> {
  const uploadIds = new Set(inputs.map((input) => input.uploadId));
  const rotation = z.literal(ROTATIONS);
  const card = z.object({
    uploadId: z.string().refine((id) => uploadIds.has(id), { error: ERRORS.chooseFile }),
    pages: pagesUpTo(maxPages),
    rotation,
  });
  const common = { compression: z.enum(COMPRESSIONS), strip_metadata: z.boolean() };
  // Só o split leva pages_per_document, só merge e split levam a rotação global e só o select
  // leva a lista: a API recusa o resto (ADR 035 e 070).
  return z.discriminatedUnion("operation", [
    z.object({ operation: z.literal("merge"), rotation, ...common }).transform((parsed) => ({
      operation: "merge" as const,
      rotation: parsed.rotation,
      compression: parsed.compression,
      strip_metadata: parsed.strip_metadata,
    })),
    z
      .object({
        operation: z.literal("split"),
        pages_per_document: integerUpTo(maxPages),
        rotation,
        ...common,
      })
      .transform((parsed) => ({
        operation: "split" as const,
        pages_per_document: parsed.pages_per_document,
        rotation: parsed.rotation,
        compression: parsed.compression,
        strip_metadata: parsed.strip_metadata,
      })),
    z
      .object({
        operation: z.literal("select"),
        cards: z.array(card).min(1, { error: ERRORS.noPages }),
        ...common,
      })
      .transform((parsed) => ({
        operation: "select" as const,
        // Um cartão vira um intervalo por parte do campo Páginas, todos com a rotação dele.
        select: parsed.cards.flatMap((chosen) =>
          chosen.pages.map((range) => ({
            upload_id: chosen.uploadId,
            first_page: range.first,
            last_page: range.last,
            rotation: chosen.rotation,
          })),
        ),
        compression: parsed.compression,
        strip_metadata: parsed.strip_metadata,
      }))
      .superRefine(({ select }, context) => {
        if (select.length > maxRanges) {
          context.addIssue({
            code: "custom",
            path: ["cards"],
            message: ERRORS.tooManyRanges(maxRanges),
          });
        } else if (closedPages(select) > maxOutputPages) {
          context.addIssue({
            code: "custom",
            path: ["cards"],
            message: ERRORS.tooManyPages(maxOutputPages),
          });
        }
      }),
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
      issues: result.error.issues.map((issue) =>
        formIssue(pdfPressField(issue.path.map(String)), issue.message),
      ),
    };
  }
  return {
    ok: true,
    command: { recipe_id: "pdf-press", recipe_version: 2, parameters: result.data },
  };
}

const ID_PREFIX = "pdf-press";
const T = TEXT.pdfPress;
type CardPart = "upload_id" | "pages" | "rotation";
const CARD_PARTS: Readonly<Record<string, CardPart>> = {
  uploadId: "upload_id",
  pages: "pages",
  rotation: "rotation",
};
// Os mesmos rótulos do cartão (PdfPressFields), contados a partir de 1.
const CARD_LABELS: Readonly<Record<CardPart, (position: number) => string>> = {
  upload_id: T.cardFile,
  pages: T.cardPages,
  rotation: T.cardRotation,
};
type SingleField =
  "operation" | "pages_per_document" | "rotation" | "compression" | "strip_metadata";
const SINGLE_LABELS: Readonly<Record<SingleField, string>> = {
  operation: T.operation,
  pages_per_document: T.pagesPerDocument,
  rotation: T.rotation,
  compression: T.compression,
  strip_metadata: T.stripMetadata,
};

/**
 * Controle para um caminho de erro relativo a `parameters`. Do formulário, `cards.n.*` aponta o
 * campo do cartão; do servidor, `select.n.*` aponta o grupo, porque um cartão gera vários
 * intervalos e o índice do servidor é o do intervalo. `null` fora do formulário.
 */
export function pdfPressField(path: readonly (string | number)[]): FormField | null {
  const [field, index, part] = path.map(String);
  if (isSingleField(field) && index === undefined) {
    return { fieldId: fieldId(field), label: SINGLE_LABELS[field] };
  }
  if (field === "select") {
    return { fieldId: addCardButtonId(), label: T.select };
  }
  if (field !== "cards") {
    return null;
  }
  if (index === undefined) {
    return { fieldId: addCardButtonId(), label: T.select };
  }
  const cardPart =
    part !== undefined && Object.hasOwn(CARD_PARTS, part) ? CARD_PARTS[part] : undefined;
  if (cardPart === undefined || !/^\d+$/.test(index)) {
    return null;
  }
  const position = Number(index);
  return { fieldId: cardFieldId(position, cardPart), label: CARD_LABELS[cardPart](position + 1) };
}

function isSingleField(field: string | undefined): field is SingleField {
  return field !== undefined && Object.hasOwn(SINGLE_LABELS, field);
}

export function fieldId(field: SingleField): string {
  return `${ID_PREFIX}-${field}`;
}

export function cardFieldId(index: number, part: CardPart): string {
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
 * arquivo tem, então merge e split falam de "todas as páginas" e o select lista os intervalos.
 */
export function pageMapPreview(
  values: PdfPressFormValues,
  inputs: readonly RecipeInput[],
  maxPage: number,
): readonly PreviewDocument[] {
  const turned = PREVIEW.rotated(values.rotation);
  switch (values.operation) {
    case "merge":
      return inputs.length === 0
        ? []
        : [
            {
              name: "merged.pdf",
              entries: inputs.map((input) => PREVIEW.allPages(input.name) + turned),
            },
          ];
    case "split": {
      const size = withoutLeadingZeros(values.pages_per_document.trim());
      const pages = PREVIEW.pages[size === "1" ? 0 : 1];
      return inputs.map((input) => ({
        name: input.name,
        entries: [PREVIEW.groups(size, pages) + turned],
      }));
    }
    case "select": {
      const names = new Map(inputs.map((input) => [input.uploadId, input.name]));
      const entries = values.cards.flatMap((card) =>
        cardEntries(names.get(card.uploadId) ?? PREVIEW.noFile, card, maxPage),
      );
      return entries.length === 0 ? [] : [{ name: "selection.pdf", entries }];
    }
  }
}

// A prévia roda antes da validação: intervalos que ainda não leem aparecem como foram digitados.
function cardEntries(name: string, card: PageCard, maxPage: number): readonly string[] {
  const turned = PREVIEW.rotated(card.rotation);
  const parsed = parsePageRanges(card.pages, maxPage);
  if (!parsed.ok) {
    return [PREVIEW.page(name, card.pages.trim()) + turned];
  }
  return parsed.ranges.map((range) => rangeEntry(name, range) + turned);
}

function rangeEntry(name: string, { first, last }: PageRangeText): string {
  if (last === null) {
    return PREVIEW.toEnd(name, first);
  }
  return first === last ? PREVIEW.page(name, String(first)) : PREVIEW.range(name, first, last);
}

// Só os dígitos viram número; o resto aparece como foi digitado.
function withoutLeadingZeros(size: string): string {
  return /^\d+$/.test(size) ? String(Number(size)) : size;
}
