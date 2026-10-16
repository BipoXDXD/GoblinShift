import { describe, expect, expectTypeOf, it } from "vitest";

import type { RecipeDraft, RecipeInput, RecipeParameters } from "../types";
import {
  pageMapPreview,
  parsePdfPress,
  pdfPressFieldId,
  type OPERATIONS,
  type PageCard,
  type PdfPressFormValues,
  type PdfPressSettings,
} from "./schema";

const SETTINGS: PdfPressSettings = {
  defaults: { operation: "merge", pages_per_document: "1", cards: [] },
  limits: { maxPages: 200, maxSelectedPages: 3 },
};
const A: RecipeInput = { uploadId: "4eb3e442-3f30-4d83-8d34-cb18a99a46cb", name: "a.pdf" };
const B: RecipeInput = { uploadId: "1b8f283e-32ee-4687-9bd1-ae2f75f93cc2", name: "b.pdf" };

function values(overrides: Partial<PdfPressFormValues> = {}): PdfPressFormValues {
  return { ...SETTINGS.defaults, ...overrides };
}

function card(input: RecipeInput | null, pageNumber: string): PageCard {
  return { key: crypto.randomUUID(), uploadId: input?.uploadId ?? "", pageNumber };
}

function parametersOf(draft: RecipeDraft<"pdf-press">) {
  if (!draft.ok) {
    throw new Error(`rascunho inválido: ${JSON.stringify(draft.issues)}`);
  }
  return draft.command.parameters;
}

function issuesOf(draft: RecipeDraft<"pdf-press">) {
  if (draft.ok) {
    throw new Error("rascunho deveria ser inválido");
  }
  return draft.issues;
}

describe("constantes espelham os tipos gerados", () => {
  it("cobrem a união de operações da spec", () => {
    expectTypeOf<(typeof OPERATIONS)[number]>().toEqualTypeOf<
      NonNullable<RecipeParameters<"pdf-press">["operation"]>
    >();
  });
});

describe("parsePdfPress", () => {
  it("merge manda só a operação", () => {
    expect(parametersOf(parsePdfPress(values(), SETTINGS, [A]))).toEqual({ operation: "merge" });
  });

  it("split manda o tamanho do grupo como número", () => {
    const draft = parsePdfPress(
      values({ operation: "split", pages_per_document: " 3 " }),
      SETTINGS,
      [],
    );

    expect(parametersOf(draft)).toEqual({ operation: "split", pages_per_document: 3 });
  });

  it.each(["0", "201", "2.5", "", "dois"])("split recusa o grupo %j", (size) => {
    const draft = parsePdfPress(
      values({ operation: "split", pages_per_document: size }),
      SETTINGS,
      [],
    );

    expect(issuesOf(draft)).toEqual([
      { fieldId: "pdf-press-pages_per_document", message: "Informe um número inteiro de 1 a 200." },
    ]);
  });

  it("select manda os cartões na ordem, com repetições", () => {
    const cards = [card(B, "2"), card(A, "1"), card(B, "2")];

    const draft = parsePdfPress(values({ operation: "select", cards }), SETTINGS, [A, B]);

    expect(parametersOf(draft)).toEqual({
      operation: "select",
      select: [
        { upload_id: B.uploadId, page_number: 2 },
        { upload_id: A.uploadId, page_number: 1 },
        { upload_id: B.uploadId, page_number: 2 },
      ],
    });
  });

  it("select sem cartões aponta o botão de adicionar", () => {
    const draft = parsePdfPress(values({ operation: "select" }), SETTINGS, [A]);

    expect(issuesOf(draft)).toEqual([
      { fieldId: "pdf-press-select-adicionar", message: "Adicione ao menos uma página." },
    ]);
  });

  it("select acima do teto aponta o botão de adicionar", () => {
    const cards = [card(A, "1"), card(A, "1"), card(A, "1"), card(A, "1")];

    const draft = parsePdfPress(values({ operation: "select", cards }), SETTINGS, [A]);

    expect(issuesOf(draft)).toEqual([
      { fieldId: "pdf-press-select-adicionar", message: "Escolha no máximo 3 páginas." },
    ]);
  });

  it("cartão de um PDF que saiu da lista, ou sem PDF, aponta o campo do arquivo", () => {
    const cards = [card(A, "1"), card(null, "1")];

    const draft = parsePdfPress(values({ operation: "select", cards }), SETTINGS, [B]);

    expect(issuesOf(draft)).toEqual([
      { fieldId: "pdf-press-select-0-upload_id", message: "Escolha um dos PDFs enviados." },
      { fieldId: "pdf-press-select-1-upload_id", message: "Escolha um dos PDFs enviados." },
    ]);
  });

  it.each(["0", "201", "-1", "x"])("cartão com a página %j aponta o campo da página", (page) => {
    const draft = parsePdfPress(values({ operation: "select", cards: [card(A, page)] }), SETTINGS, [
      A,
    ]);

    expect(issuesOf(draft)).toEqual([
      {
        fieldId: "pdf-press-select-0-page_number",
        message: "Informe um número inteiro de 1 a 200.",
      },
    ]);
  });

  it("páginas 1 e 200 são as fronteiras aceitas", () => {
    const cards = [card(A, "1"), card(A, "200")];

    expect(parsePdfPress(values({ operation: "select", cards }), SETTINGS, [A]).ok).toBe(true);
  });
});

describe("pdfPressFieldId", () => {
  it.each([
    [["operation"], "pdf-press-operation"],
    [["pages_per_document"], "pdf-press-pages_per_document"],
    [["select"], "pdf-press-select-adicionar"],
    [["select", "1", "upload_id"], "pdf-press-select-1-upload_id"],
    [["select", 2, "page_number"], "pdf-press-select-2-page_number"],
  ] as const)("traduz %j do servidor", (path, id) => {
    expect(pdfPressFieldId(path)).toBe(id);
  });

  it.each([[["outro"]], [["select", "x", "upload_id"]], [["select", "0", "outro"]], [[]]])(
    "fora do formulário %j → null",
    (path) => {
      expect(pdfPressFieldId(path)).toBeNull();
    },
  );
});

describe("pageMapPreview", () => {
  it("merge junta todos os PDFs na ordem", () => {
    expect(pageMapPreview(values(), [A, B])).toEqual([
      { name: "merged.pdf", entries: ["a.pdf, todas as páginas", "b.pdf, todas as páginas"] },
    ]);
  });

  it("split descreve as partes de cada PDF", () => {
    expect(pageMapPreview(values({ operation: "split", pages_per_document: "2" }), [A])).toEqual([
      { name: "a.pdf", entries: ["partes de 2 página(s), na ordem"] },
    ]);
  });

  it("select lista os cartões, inclusive o sem PDF", () => {
    const cards = [card(B, "3"), card(null, "1")];

    expect(pageMapPreview(values({ operation: "select", cards }), [A, B])).toEqual([
      { name: "selection.pdf", entries: ["b.pdf, página 3", "PDF não escolhido, página 1"] },
    ]);
  });

  it("sem PDFs nem cartões não há mapa", () => {
    expect(pageMapPreview(values(), [])).toEqual([]);
    expect(pageMapPreview(values({ operation: "select" }), [A])).toEqual([]);
  });
});
