import { describe, expect, expectTypeOf, it } from "vitest";

import type { RecipeDraft, RecipeInput, RecipeParameters } from "../types";
import {
  pageMapPreview,
  parsePdfPress,
  pdfPressField,
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
      {
        fieldId: "pdf-press-pages_per_document",
        label: "Páginas por parte",
        message: "Informe um número inteiro de 1 a 200.",
      },
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
      {
        fieldId: "pdf-press-select-adicionar",
        label: "Páginas escolhidas",
        message: "Adicione ao menos uma página.",
      },
    ]);
  });

  it("select acima do teto aponta o botão de adicionar", () => {
    const cards = [card(A, "1"), card(A, "1"), card(A, "1"), card(A, "1")];

    const draft = parsePdfPress(values({ operation: "select", cards }), SETTINGS, [A]);

    expect(issuesOf(draft)).toEqual([
      {
        fieldId: "pdf-press-select-adicionar",
        label: "Páginas escolhidas",
        message: "Escolha no máximo 3 páginas.",
      },
    ]);
  });

  it("cartão de um PDF que saiu da lista, ou sem PDF, aponta o campo do arquivo", () => {
    const cards = [card(A, "1"), card(null, "1")];

    const draft = parsePdfPress(values({ operation: "select", cards }), SETTINGS, [B]);

    expect(issuesOf(draft)).toEqual([
      {
        fieldId: "pdf-press-select-0-upload_id",
        label: "PDF da página 1",
        message: "Escolha um dos PDFs enviados.",
      },
      {
        fieldId: "pdf-press-select-1-upload_id",
        label: "PDF da página 2",
        message: "Escolha um dos PDFs enviados.",
      },
    ]);
  });

  it.each(["0", "201", "-1", "x"])("cartão com a página %j aponta o campo da página", (page) => {
    const draft = parsePdfPress(values({ operation: "select", cards: [card(A, page)] }), SETTINGS, [
      A,
    ]);

    expect(issuesOf(draft)).toEqual([
      {
        fieldId: "pdf-press-select-0-page_number",
        label: "Número da página 1 no PDF",
        message: "Informe um número inteiro de 1 a 200.",
      },
    ]);
  });

  it("páginas 1 e 200 são as fronteiras aceitas", () => {
    const cards = [card(A, "1"), card(A, "200")];

    expect(parsePdfPress(values({ operation: "select", cards }), SETTINGS, [A]).ok).toBe(true);
  });
});

describe("pdfPressField", () => {
  it.each([
    [["operation"], "pdf-press-operation", "O que fazer com os PDFs"],
    [["pages_per_document"], "pdf-press-pages_per_document", "Páginas por parte"],
    [["select"], "pdf-press-select-adicionar", "Páginas escolhidas"],
    [["select", "1", "upload_id"], "pdf-press-select-1-upload_id", "PDF da página 2"],
    [["select", 2, "page_number"], "pdf-press-select-2-page_number", "Número da página 3 no PDF"],
  ] as const)("traduz %j do servidor", (path, fieldId, label) => {
    expect(pdfPressField(path)).toEqual({ fieldId, label });
  });

  it.each([[["outro"]], [["select", "x", "upload_id"]], [["select", "0", "outro"]], [[]]])(
    "fora do formulário %j → null",
    (path) => {
      expect(pdfPressField(path)).toBeNull();
    },
  );
});

describe("pageMapPreview", () => {
  it("merge junta todos os PDFs na ordem", () => {
    expect(pageMapPreview(values(), [A, B])).toEqual([
      { name: "merged.pdf", entries: ["a.pdf, todas as páginas", "b.pdf, todas as páginas"] },
    ]);
  });

  it("split descreve as partes de cada PDF, no plural", () => {
    expect(pageMapPreview(values({ operation: "split", pages_per_document: "2" }), [A])).toEqual([
      { name: "a.pdf", entries: ["partes de 2 páginas, na ordem"] },
    ]);
  });

  it.each(["1", "01"])(
    "split com %j página por parte usa o singular, sem zero à esquerda",
    (size) => {
      expect(pageMapPreview(values({ operation: "split", pages_per_document: size }), [A])).toEqual(
        [{ name: "a.pdf", entries: ["partes de 1 página, na ordem"] }],
      );
    },
  );

  it("split mostra o tamanho sem zeros à esquerda também no plural", () => {
    expect(pageMapPreview(values({ operation: "split", pages_per_document: "007" }), [A])).toEqual([
      { name: "a.pdf", entries: ["partes de 7 páginas, na ordem"] },
    ]);
  });

  it("split com tamanho que não é número mostra o que foi digitado", () => {
    expect(pageMapPreview(values({ operation: "split", pages_per_document: "dois" }), [A])).toEqual(
      [{ name: "a.pdf", entries: ["partes de dois páginas, na ordem"] }],
    );
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
